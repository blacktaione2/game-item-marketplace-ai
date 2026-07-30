package com.gimp.backend.service;

import com.gimp.backend.domain.item.Item;
import com.gimp.backend.domain.item.SaleType;
import com.gimp.backend.domain.trade.Trade;
import com.gimp.backend.domain.trade.TradeStatus;
import com.gimp.backend.domain.trade.TradeType;
import com.gimp.backend.domain.user.User;
import com.gimp.backend.dto.trade.TradeResponse;
import com.gimp.backend.exception.InvalidTradeRequestException;
import com.gimp.backend.exception.LockAcquisitionException;
import com.gimp.backend.exception.ResourceNotFoundException;
import com.gimp.backend.repository.ItemRepository;
import com.gimp.backend.repository.TradeRepository;
import com.gimp.backend.repository.UserRepository;
import java.math.BigDecimal;
import java.util.concurrent.TimeUnit;
import org.redisson.api.RLock;
import org.redisson.api.RedissonClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;

/**
 * 구매/입찰은 AI/큐 처리 없이 "Redis 분산 락(동시성 제어) + DB 트랜잭션(원자성)"만으로 처리한다
 * (로드맵 Phase 1 범위 — RabbitMQ 비동기 처리는 이후 단계에서 붙인다).
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

    public TradeService(
            RedissonClient redissonClient,
            ItemRepository itemRepository,
            UserRepository userRepository,
            TradeRepository tradeRepository,
            PlatformTransactionManager transactionManager) {
        this.redissonClient = redissonClient;
        this.itemRepository = itemRepository;
        this.userRepository = userRepository;
        this.tradeRepository = tradeRepository;
        this.transactionTemplate = new TransactionTemplate(transactionManager);
    }

    public TradeResponse purchase(Long tenantId, Long itemId, Long buyerId, int quantity) {
        return withItemLock(itemId, () -> doPurchase(tenantId, itemId, buyerId, quantity));
    }

    public TradeResponse bid(Long tenantId, Long itemId, Long bidderId, BigDecimal bidPrice) {
        return withItemLock(itemId, () -> doBid(tenantId, itemId, bidderId, bidPrice));
    }

    private TradeResponse withItemLock(Long itemId, java.util.function.Supplier<TradeResponse> action) {
        RLock lock = redissonClient.getLock(LOCK_KEY_PREFIX + itemId);
        boolean acquired;
        try {
            acquired = lock.tryLock(LOCK_WAIT_SECONDS, LOCK_LEASE_SECONDS, TimeUnit.SECONDS);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new LockAcquisitionException("잠금 획득 중 인터럽트가 발생했습니다.");
        }
        if (!acquired) {
            throw new LockAcquisitionException("다른 거래가 처리 중입니다. 잠시 후 다시 시도해주세요.");
        }
        try {
            return transactionTemplate.execute(status -> action.get());
        } finally {
            if (lock.isHeldByCurrentThread()) {
                lock.unlock();
            }
        }
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

        return TradeResponse.from(tradeRepository.save(trade));
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

        tradeRepository.findByItemIdAndStatus(itemId, TradeStatus.ACTIVE).ifPresent(Trade::markOutbid);
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

        return TradeResponse.from(tradeRepository.save(trade));
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
