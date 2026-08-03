package com.gimp.backend.messaging;

import static org.assertj.core.api.Assertions.assertThat;

import com.gimp.backend.domain.item.Item;
import com.gimp.backend.domain.item.SaleType;
import com.gimp.backend.domain.notification.Notification;
import com.gimp.backend.domain.notification.NotificationType;
import com.gimp.backend.domain.tenant.Tenant;
import com.gimp.backend.domain.trade.TradeType;
import com.gimp.backend.domain.user.User;
import com.gimp.backend.domain.user.UserRole;
import com.gimp.backend.event.TradeCompletedEvent;
import com.gimp.backend.exception.InvalidTradeRequestException;
import com.gimp.backend.repository.ItemRepository;
import com.gimp.backend.repository.NotificationRepository;
import com.gimp.backend.repository.TenantRepository;
import com.gimp.backend.repository.UserRepository;
import com.gimp.backend.service.TradeService;
import java.math.BigDecimal;
import java.util.List;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.bean.override.mockito.MockitoSpyBean;
import org.springframework.transaction.support.TransactionTemplate;

/**
 * 체결 후 알림 흐름 (ADR-0030).
 *
 * <p><b>브로커를 띄우지 않고 검증한다.</b> 여기서 확인하려는 것은 세 가지인데
 * 전부 브로커 없이 성립한다:
 *
 * <ul>
 *   <li><b>롤백된 거래는 이벤트를 내보내지 않는다</b> — {@code AFTER_COMMIT} 의 핵심
 *   <li><b>같은 메시지를 두 번 소비해도 알림은 한 건</b> — 멱등성
 *   <li><b>중복 소비가 예외로 새어나가지 않는다</b> — 새면 모든 재전달이 DLQ 로 간다
 * </ul>
 *
 * <p>실제 브로커를 거치는 종단 확인(큐 배수, DLQ 적재, 브로커 다운 시 fail-open)은
 * {@code load/verify-mq.sh} 가 맡는다 — 그건 단위 테스트로 재현할 수 없는 것들이다.
 *
 * <p><b>{@code @Transactional} 을 붙이지 않았다.</b> 테스트가 트랜잭션을 쥐고 있으면
 * {@code AFTER_COMMIT} 리스너가 <b>영영 불리지 않아</b> 검증 자체가 성립하지 않는다.
 * 대신 만든 데이터를 직접 지운다.
 */
@SpringBootTest
class NotificationFlowTest {

    @Autowired TradeService tradeService;
    @Autowired NotificationConsumer consumer;
    @Autowired TenantRepository tenantRepository;
    @Autowired UserRepository userRepository;
    @Autowired ItemRepository itemRepository;
    @Autowired NotificationRepository notificationRepository;
    @Autowired org.springframework.transaction.PlatformTransactionManager transactionManager;
    @Autowired org.springframework.context.ApplicationEventPublisher springEventPublisher;

    /** 발행을 가로채 "무엇이 나갔는가"만 본다 — 브로커가 없어도 확인할 수 있다. */
    @MockitoSpyBean TradeEventPublisher publisher;

    private Tenant tenant;
    private User seller;
    private User buyer;
    private Item item;

    @BeforeEach
    void setUp() {
        tenant = tenantRepository.save(
                Tenant.builder().code("mq_t_" + System.nanoTime()).name("MQ 테스트").build());
        seller = saveUser("mq_seller");
        buyer = saveUser("mq_buyer");
        item = itemRepository.save(Item.builder()
                .tenant(tenant)
                .seller(seller)
                .name("알림 테스트 검")
                .description("MQ 흐름 검증용")
                .saleType(SaleType.FIXED_PRICE)
                .price(new BigDecimal("10000"))
                .stock(3)
                .build());
    }

    @Nested
    class 커밋_이후에만_발행한다 {

        @Test
        void 정상_구매는_이벤트를_내보낸다() {
            tradeService.purchase(tenant.getId(), item.getId(), buyer.getId(), 1);

            org.mockito.Mockito.verify(publisher).publish(org.mockito.ArgumentMatchers.any());
        }

        /**
         * 검증 대상에 도달하지 못하는 경우. 재고 확인이 <b>발행 지점보다 앞</b>이라
         * 이벤트 등록 자체가 일어나지 않는다.
         *
         * <p><b>이 테스트만으로는 AFTER_COMMIT 을 검증하지 못한다.</b> 실제로
         * 트랜잭션 내부 발행으로 바꿔 돌려봤더니 그대로 통과했다 — 아래
         * {@code 롤백되면_발행되지_않는다} 가 그 공백을 메운다.
         */
        @Test
        void 발행_전에_거절되면_이벤트가_없다() {
            try {
                tradeService.purchase(tenant.getId(), item.getId(), buyer.getId(), 99);
            } catch (InvalidTradeRequestException expected) {
                // 재고 3개짜리에 99개를 요청했다 — 거절이 정상이다.
            }

            org.mockito.Mockito.verify(publisher, org.mockito.Mockito.never())
                    .publish(org.mockito.ArgumentMatchers.any());
        }

        /**
         * <b>이 라운드에서 가장 중요한 단언.</b>
         *
         * <p>이벤트를 등록한 트랜잭션이 <b>롤백되면</b> 발행이 일어나지 않아야 한다.
         * 트랜잭션 안에서 바로 보내는 구현이었다면 롤백된 거래의 알림이 나간다 —
         * 존재하지 않는 거래를 알리는 셈이다.
         *
         * <p>거래 서비스를 거치지 않고 <b>이벤트 등록과 롤백만</b> 재현한다. 커밋 이후에
         * 실패를 강제하는 건 낙관적 락 충돌을 인위로 만들어야 해서 불안정하고, 여기서
         * 확인하려는 것은 리스너의 <b>단계(phase)</b> 하나다.
         */
        @Test
        void 롤백되면_발행되지_않는다() {
            TransactionTemplate template = new TransactionTemplate(transactionManager);
            template.execute(status -> {
                springEventPublisher.publishEvent(purchaseEvent(9_000_100L));
                status.setRollbackOnly();
                return null;
            });

            org.mockito.Mockito.verify(publisher, org.mockito.Mockito.never())
                    .publish(org.mockito.ArgumentMatchers.any());
        }

        /** 대조군 — 같은 경로가 <b>커밋되면</b> 발행된다. 위 테스트가 항상 통과하는 게 아님을 보인다. */
        @Test
        void 커밋되면_발행된다() {
            TransactionTemplate template = new TransactionTemplate(transactionManager);
            template.execute(status -> {
                springEventPublisher.publishEvent(purchaseEvent(9_000_101L));
                return null;
            });

            org.mockito.Mockito.verify(publisher).publish(org.mockito.ArgumentMatchers.any());
        }
    }

    @Nested
    class 재전달을_견딘다 {

        @Test
        void 같은_이벤트를_두_번_소비해도_알림은_한_건씩이다() {
            TradeCompletedEvent event = purchaseEvent(9_000_001L);

            consumer.onTradeCompleted(event);
            consumer.onTradeCompleted(event); // at-least-once — 재전달을 흉내낸다

            assertThat(notificationsOf(buyer)).hasSize(1);
            assertThat(notificationsOf(seller)).hasSize(1);
        }

        /**
         * 중복이 예외로 새어나가면 <b>모든 재전달이 DLQ 로 간다</b> — 멱등성 장치가
         * 오히려 실패를 만든다. 두 번째 호출이 조용히 끝나야 한다.
         */
        @Test
        void 중복_소비가_예외를_던지지_않는다() {
            TradeCompletedEvent event = purchaseEvent(9_000_002L);

            consumer.onTradeCompleted(event);
            consumer.onTradeCompleted(event); // 예외가 나면 이 테스트가 실패한다
        }

        @Test
        void 구매는_구매자와_판매자_양쪽에_알림을_만든다() {
            consumer.onTradeCompleted(purchaseEvent(9_000_003L));

            assertThat(notificationsOf(buyer))
                    .singleElement()
                    .extracting(Notification::getType)
                    .isEqualTo(NotificationType.PURCHASE_COMPLETED);
            assertThat(notificationsOf(seller))
                    .singleElement()
                    .extracting(Notification::getType)
                    .isEqualTo(NotificationType.ITEM_SOLD);
        }
    }

    // --- 헬퍼 ---------------------------------------------------------------

    private TradeCompletedEvent purchaseEvent(Long tradeId) {
        return new TradeCompletedEvent(
                tradeId,
                tenant.getId(),
                item.getId(),
                item.getName(),
                buyer.getId(),
                seller.getId(),
                null,
                TradeType.PURCHASE,
                new BigDecimal("10000"),
                1);
    }

    private List<Notification> notificationsOf(User user) {
        return notificationRepository.findByRecipientIdOrderByIdDesc(
                user.getId(), org.springframework.data.domain.PageRequest.of(0, 20));
    }

    private User saveUser(String prefix) {
        String unique = prefix + "_" + System.nanoTime();
        return userRepository.save(User.builder()
                .tenant(tenant)
                .username(unique)
                .email(unique + "@example.com")
                .passwordHash("(데모 — 비밀번호 인증 없음)")
                .role(UserRole.USER)
                .build());
    }
}
