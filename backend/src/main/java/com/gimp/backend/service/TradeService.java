package com.gimp.backend.service;

import com.gimp.backend.domain.item.Item;
import com.gimp.backend.domain.item.SaleType;
import com.gimp.backend.domain.trade.Trade;
import com.gimp.backend.domain.trade.TradeStatus;
import com.gimp.backend.domain.trade.TradeType;
import com.gimp.backend.domain.user.User;
import com.gimp.backend.dto.trade.TradeResponse;
import com.gimp.backend.event.TradeCompletedEvent;
import com.gimp.backend.exception.InvalidTradeRequestException;
import com.gimp.backend.exception.LockAcquisitionException;
import com.gimp.backend.exception.ResourceNotFoundException;
import com.gimp.backend.repository.ItemRepository;
import com.gimp.backend.repository.TradeRepository;
import com.gimp.backend.repository.UserRepository;
import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Timer;
import java.math.BigDecimal;
import java.util.concurrent.TimeUnit;
import org.redisson.api.RLock;
import org.redisson.api.RedissonClient;
import org.springframework.context.ApplicationEventPublisher;
import org.springframework.stereotype.Service;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;

/**
 * 구매/입찰 <b>체결 자체는</b> "Redis 분산 락(동시성 제어) + DB 트랜잭션(원자성)"만으로 처리한다.
 *
 * <p><b>큐로 넘긴 것은 체결이 아니라 그 뒤다</b>(ADR-0030). 계획서는 거래 처리 자체를
 * RabbitMQ 로 보내는 그림이었지만, 그러면 API 가 202 를 반환하게 되고 ADR-0020 이
 * 실동시성 26,600 건에서 검증한 {@code 재고 감소분 == 성공 응답 수} 단언이 응답 시점에
 * 성립하지 않는다. 그 보장을 지키는 쪽을 택했고, 큐는 계획서 4단계(체결 후 알림)를 맡는다.
 *
 * <p>락은 {@code @Transactional} 프록시 바깥에서 걸어야 하므로, 이 클래스 자신을 self-invocation
 * 하는 대신 {@link TransactionTemplate}으로 트랜잭션 경계를 명시적으로 감싼다. (같은 클래스의
 * {@code @Transactional} 메서드를 this로 직접 호출하면 프록시를 우회해 트랜잭션이 걸리지 않는
 * 함정이 있음.)
 */
@Service
public class TradeService {

    private static final String LOCK_KEY_PREFIX = "lock:item:";
    private static final long LOCK_WAIT_SECONDS = 3L;
    private static final long LOCK_LEASE_SECONDS = 5L;

    private final RedissonClient redissonClient;
    private final ItemRepository itemRepository;
    private final UserRepository userRepository;
    private final TradeRepository tradeRepository;
    private final TransactionTemplate transactionTemplate;
    private final MeterRegistry meterRegistry;
    private final ApplicationEventPublisher eventPublisher;

    public TradeService(
            RedissonClient redissonClient,
            ItemRepository itemRepository,
            UserRepository userRepository,
            TradeRepository tradeRepository,
            PlatformTransactionManager transactionManager,
            MeterRegistry meterRegistry,
            ApplicationEventPublisher eventPublisher) {
        this.redissonClient = redissonClient;
        this.itemRepository = itemRepository;
        this.userRepository = userRepository;
        this.tradeRepository = tradeRepository;
        this.transactionTemplate = new TransactionTemplate(transactionManager);
        this.meterRegistry = meterRegistry;
        this.eventPublisher = eventPublisher;
    }

    public TradeResponse purchase(Long tenantId, Long itemId, Long buyerId, int quantity) {
        return withItemLock(tenantId, itemId, () -> doPurchase(tenantId, itemId, buyerId, quantity));
    }

    public TradeResponse bid(Long tenantId, Long itemId, Long bidderId, BigDecimal bidPrice) {
        return withItemLock(tenantId, itemId, () -> doBid(tenantId, itemId, bidderId, bidPrice));
    }

    /**
     * 락을 걸고 트랜잭션 안에서 실행한다.
     *
     * <p>여기가 고경합 시나리오의 유일한 시임이라 계측도 이 자리에 모은다. 부하테스트에서
     * "락이 병목인가"를 답하려면 <b>대기</b>와 <b>보유</b>를 갈라야 한다 — 대기가 길면 경합이,
     * 보유가 길면 트랜잭션 자체가 문제다. 합쳐서 재면 둘을 구분할 수 없다.
     *
     * <p>태그는 tenant까지만 붙인다. itemId는 무한히 늘어나는 값이라 라벨로 쓰면 시계열이
     * 폭발한다 — "어떤 아이템이 경합했나"는 메트릭이 아니라 로그로 답할 문제다.
     */
    private TradeResponse withItemLock(
            Long tenantId, Long itemId, java.util.function.Supplier<TradeResponse> action) {
        String tenant = String.valueOf(tenantId);
        RLock lock = redissonClient.getLock(LOCK_KEY_PREFIX + itemId);

        boolean acquired;
        Timer.Sample waitSample = Timer.start(meterRegistry);
        try {
            acquired = lock.tryLock(LOCK_WAIT_SECONDS, LOCK_LEASE_SECONDS, TimeUnit.SECONDS);
        } catch (InterruptedException e) {
            recordLockWait(waitSample, tenant, "interrupted");
            Thread.currentThread().interrupt();
            throw new LockAcquisitionException("잠금 획득 중 인터럽트가 발생했습니다.");
        }
        recordLockWait(waitSample, tenant, acquired ? "acquired" : "timeout");

        if (!acquired) {
            throw new LockAcquisitionException("다른 거래가 처리 중입니다. 잠시 후 다시 시도해주세요.");
        }

        Timer.Sample holdSample = Timer.start(meterRegistry);
        try {
            return transactionTemplate.execute(status -> action.get());
        } finally {
            holdSample.stop(
                    Timer.builder("trade.lock.hold")
                            .description("락을 쥔 채 트랜잭션을 실행한 시간")
                            .tag("tenant", tenant)
                            .register(meterRegistry));
            if (lock.isHeldByCurrentThread()) {
                lock.unlock();
            }
        }
    }

    private void recordLockWait(Timer.Sample sample, String tenant, String outcome) {
        sample.stop(
                Timer.builder("trade.lock.wait")
                        .description("tryLock 호출부터 반환까지 — 경합 시 대기 시간")
                        .tag("tenant", tenant)
                        .tag("outcome", outcome)
                        .register(meterRegistry));
    }

    private TradeResponse doPurchase(Long tenantId, Long itemId, Long buyerId, int quantity) {
        Item item = getItem(tenantId, itemId);
        if (item.getSaleType() != SaleType.FIXED_PRICE) {
            throw new InvalidTradeRequestException("정가 판매 아이템만 구매할 수 있습니다.");
        }
        if (!item.isOnSale()) {
            throw new InvalidTradeRequestException("판매중인 아이템이 아닙니다.");
        }
        if (item.getStock() < quantity) {
            throw new InvalidTradeRequestException("재고가 부족합니다.");
        }

        User buyer = getUser(tenantId, buyerId);
        if (buyer.getId().equals(item.getSeller().getId())) {
            throw new InvalidTradeRequestException("본인이 등록한 아이템은 구매할 수 없습니다.");
        }

        item.decreaseStock(quantity);

        Trade trade = Trade.builder()
                .tenant(item.getTenant())
                .item(item)
                .buyer(buyer)
                .seller(item.getSeller())
                .tradeType(TradeType.PURCHASE)
                .price(item.getPrice())
                .quantity(quantity)
                .status(TradeStatus.COMPLETED)
                .build();

        Trade saved = tradeRepository.save(trade);
        publishCompleted(saved, item, null);
        return TradeResponse.from(saved);
    }

    private TradeResponse doBid(Long tenantId, Long itemId, Long bidderId, BigDecimal bidPrice) {
        Item item = getItem(tenantId, itemId);
        if (item.getSaleType() != SaleType.AUCTION) {
            throw new InvalidTradeRequestException("경매 아이템에만 입찰할 수 있습니다.");
        }
        if (!item.isOnSale()) {
            throw new InvalidTradeRequestException("판매중인 아이템이 아닙니다.");
        }

        User bidder = getUser(tenantId, bidderId);
        if (bidder.getId().equals(item.getSeller().getId())) {
            throw new InvalidTradeRequestException("본인이 등록한 아이템에는 입찰할 수 없습니다.");
        }

        if (bidPrice.compareTo(item.minimumAcceptableBid()) <= 0) {
            throw new InvalidTradeRequestException("현재 최고 입찰가보다 높은 금액을 입력해주세요.");
        }

        // 밀려나는 입찰자를 이벤트에 실어야 하므로 갱신 전에 붙잡아 둔다.
        Trade previous = tradeRepository
                .findByTenantIdAndItemIdAndStatus(tenantId, itemId, TradeStatus.ACTIVE)
                .orElse(null);
        Long previousBidderId = previous != null ? previous.getBuyer().getId() : null;
        if (previous != null) {
            previous.markOutbid();
        }
        item.placeBid(bidPrice, bidder);

        Trade trade = Trade.builder()
                .tenant(item.getTenant())
                .item(item)
                .buyer(bidder)
                .seller(item.getSeller())
                .tradeType(TradeType.BID)
                .price(bidPrice)
                .quantity(1)
                .status(TradeStatus.ACTIVE)
                .build();

        Trade saved = tradeRepository.save(trade);
        publishCompleted(saved, item, previousBidderId);
        return TradeResponse.from(saved);
    }

    /**
     * 체결 이벤트를 등록한다 (ADR-0030).
     *
     * <p><b>여기서 브로커로 바로 보내지 않는다.</b> 이 메서드는 트랜잭션 안에서 불리므로,
     * 지금 발행하면 <b>롤백된 거래의 알림</b>이 나갈 수 있다. 스프링 이벤트로 등록만 하고
     * 실제 발행은 {@code TradeEventPublisher} 가 {@code AFTER_COMMIT} 에서 한다.
     *
     * <p>{@code saved.getId()} 를 읽으려면 식별자가 필요한데, {@code IDENTITY} 전략이라
     * {@code save()} 시점에 insert 가 나가면서 이미 채워져 있다.
     */
    private void publishCompleted(Trade trade, Item item, Long previousBidderId) {
        eventPublisher.publishEvent(new TradeCompletedEvent(
                trade.getId(),
                item.getTenant().getId(),
                item.getId(),
                item.getName(),
                trade.getBuyer().getId(),
                item.getSeller().getId(),
                previousBidderId,
                trade.getTradeType(),
                trade.getPrice(),
                trade.getQuantity()));
    }

    private Item getItem(Long tenantId, Long itemId) {
        return itemRepository
                .findByIdAndTenantId(itemId, tenantId)
                .orElseThrow(() -> new ResourceNotFoundException("아이템을 찾을 수 없습니다. id=" + itemId));
    }

    private User getUser(Long tenantId, Long userId) {
        return userRepository
                .findByIdAndTenantId(userId, tenantId)
                .orElseThrow(() -> new ResourceNotFoundException("유저를 찾을 수 없습니다. id=" + userId));
    }
}
