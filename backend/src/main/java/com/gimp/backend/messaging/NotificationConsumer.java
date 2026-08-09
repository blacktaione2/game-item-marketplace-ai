package com.gimp.backend.messaging;

import com.gimp.backend.config.RabbitConfig;
import com.gimp.backend.domain.notification.Notification;
import com.gimp.backend.domain.notification.NotificationType;
import com.gimp.backend.domain.tenant.Tenant;
import com.gimp.backend.domain.user.User;
import com.gimp.backend.event.TradeCompletedEvent;
import com.gimp.backend.repository.NotificationRepository;
import com.gimp.backend.repository.TenantRepository;
import com.gimp.backend.repository.UserRepository;
import java.util.ArrayList;
import java.util.List;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.amqp.rabbit.annotation.RabbitListener;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

/**
 * 체결 이벤트를 소비해 알림을 만든다 (ADR-0030).
 *
 * <h2>재전달은 실패가 아니다</h2>
 *
 * RabbitMQ 는 <b>at-least-once</b> 다. 커밋은 됐는데 ack 가 실패하면 같은 메시지가 다시 온다.
 * 그래서 {@code (recipient_id, trade_id)} 에 unique 를 걸어 중복을 막는데, <b>여기서
 * 놓치기 쉬운 것</b>이 있다 — 그 제약 위반을 예외로 흘려보내면 리스너가 실패로 처리되고
 * <b>모든 재전달이 DLQ 로 간다.</b> 멱등성 장치가 오히려 실패를 만드는 셈이다.
 *
 * <p>그래서 저장 전에 <b>먼저 물어본다</b>({@code existsByRecipientIdAndTradeId}).
 * 리스너 동시성이 1이라 현실적인 재전달은 전부 여기서 걸린다.
 *
 * <p><b>2026-08-10 정정 (ADR-0048)</b> — 이 자리에 원래
 * <i>"{@code DuplicateKeyException} 을 잡아 정상 종료한다"</i> 가 있었고,
 * 상위 타입을 피한 근거(FK 위반까지 삼키면 안 된다)까지 적혀 있었다. 근거는 옳지만
 * <b>그 예외가 오지 않는다</b> — JPA 경로에서 제약 위반은
 * {@code DataIntegrityViolationException} 으로 번역된다. 게다가 타입을 넓혀도
 * 소용없다: flush 가 제약을 위반하면 트랜잭션이 rollback-only 라 커밋에서 실패한다.
 * 두 사실 모두 {@code NotificationFlowTest} 가 잰다. 그래서 {@code catch} 를 지웠고,
 * 드문 경쟁은 <b>재시도</b>가 막는다(재시도 때는 사전확인에 걸린다).
 *
 * <h2>순서는 보장하지 않는다</h2>
 *
 * 계획서는 "컨슈머가 순서대로 처리"라고 적지만, <b>알림에는 순서가 무의미하다</b> —
 * 거래마다 독립이고 서로를 덮어쓰지 않는다. 순서가 필요해지는 것은 같은 아이템의 시세를
 * 순차 갱신할 때인데, 그 소비자는 아직 만들 수 없다(시세 예측이 Postgres 가 아니라
 * 합성 코퍼스를 읽는다 — id 공간 통합에 묶인 항목이다).
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class NotificationConsumer {

    private final NotificationRepository notificationRepository;
    private final UserRepository userRepository;
    private final TenantRepository tenantRepository;

    @RabbitListener(queues = RabbitConfig.QUEUE)
    @Transactional
    public void onTradeCompleted(TradeCompletedEvent event) {
        Tenant tenant = tenantRepository.getReferenceById(event.tenantId());
        List<Notification> pending = new ArrayList<>();

        if (event.tradeType() == com.gimp.backend.domain.trade.TradeType.PURCHASE) {
            pending.add(
                    build(tenant, event.buyerId(), event, NotificationType.PURCHASE_COMPLETED,
                            "'%s' 구매가 완료되었습니다.".formatted(event.itemName())));
            pending.add(
                    build(tenant, event.sellerId(), event, NotificationType.ITEM_SOLD,
                            "'%s'이(가) 판매되었습니다.".formatted(event.itemName())));
        } else {
            pending.add(
                    build(tenant, event.buyerId(), event, NotificationType.BID_PLACED,
                            "'%s'에 입찰하였습니다.".formatted(event.itemName())));
            if (event.previousBidderId() != null) {
                pending.add(
                        build(tenant, event.previousBidderId(), event, NotificationType.OUTBID,
                                "'%s'에서 더 높은 입찰이 들어왔습니다.".formatted(event.itemName())));
            }
        }

        for (Notification notification : pending) {
            save(notification, event.tradeId());
        }
    }

    /**
     * 한 건씩 저장한다. 묶어서 저장하면 <b>한 건의 중복이 나머지까지 되돌린다</b> —
     * 구매자 알림은 이미 있고 판매자 알림은 없는 부분 재전달에서 실제로 갈린다.
     *
     * <p><b>여기 {@code catch (DuplicateKeyException)} 이 있었고, 지웠다</b> (ADR-0048).
     * 사전확인과 insert 사이의 경쟁을 삼켜서 DLQ 행을 막겠다는 것이었는데, 실측해 보니
     * <b>두 겹으로 동작하지 않았다.</b>
     *
     * <ol>
     *   <li><b>그 예외는 던져지지 않는다.</b> JPA 경로에서 제약 위반은
     *       {@code HibernateExceptionTranslator} 를 거쳐
     *       {@code DataIntegrityViolationException} 이 된다. 좁은 타입을 고른 근거
     *       (<i>"상위 타입은 FK 위반까지 삼킨다"</i>)는 옳지만, <b>고른 타입이 오지
     *       않는다.</b>
     *   <li><b>타입을 넓혀도 소용없다.</b> flush 가 제약을 위반하면 트랜잭션이
     *       rollback-only 로 표시되므로, 삼키고 정상 반환해도 커밋에서 실패한다.
     * </ol>
     *
     * <p>둘을 합치면 <b>그 자리에서 삼켜 성공으로 만드는 것은 불가능하다.</b> 실제 방어는
     * 원래부터 둘이었다 — 사전확인이 모든 현실적 재전달을 막고, 드문 경쟁은 <b>재시도</b>가
     * 막는다(재시도 때는 행이 이미 있으므로 사전확인에 걸린다). 없어진 것은 보호가 아니라
     * <b>보호한다는 주장</b>이다.
     *
     * <p>{@code NotificationFlowTest} 의 두 검사가 위 ①②를 각각 고정한다. 그 전까지
     * 이 자리를 덮는다고 여겨졌던 {@code 중복_소비가_예외를_던지지_않는다} 는 사전확인에서
     * 조기 반환해 <b>{@code catch} 에 닿은 적이 없었다.</b>
     */
    private void save(Notification notification, Long tradeId) {
        Long recipientId = notification.getRecipient().getId();
        // 먼저 물어본다. **현실적인 재전달은 전부 여기서 걸린다** — 리스너 동시성이
        // 1이라 사전확인과 insert 사이에 끼어들 다른 소비자가 없다.
        if (notificationRepository.existsByRecipientIdAndTradeId(recipientId, tradeId)) {
            return;
        }
        notificationRepository.saveAndFlush(notification);
    }

    private Notification build(
            Tenant tenant, Long recipientId, TradeCompletedEvent event,
            NotificationType type, String message) {
        User recipient = userRepository.getReferenceById(recipientId);
        return Notification.builder()
                .tenant(tenant)
                .recipient(recipient)
                .tradeId(event.tradeId())
                .type(type)
                .message(message)
                .build();
    }
}
