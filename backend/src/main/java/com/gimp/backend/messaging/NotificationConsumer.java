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
import org.springframework.dao.DuplicateKeyException;
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
 * <p>그래서 {@link DuplicateKeyException} 을 잡아 <b>정상 종료</b>한다.
 * "이미 처리했다"는 뜻이지 오류가 아니다.
 *
 * <p><b>상위 타입인 {@code DataIntegrityViolationException} 을 잡으면 안 된다.</b>
 * 그건 FK 위반까지 함께 삼켜서, 존재하지 않는 유저를 가리키는 메시지가 조용히
 * 사라진다 — 그런 메시지는 재시도 후 DLQ 로 가서 사람 눈에 띄어야 한다.
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
     */
    private void save(Notification notification, Long tradeId) {
        Long recipientId = notification.getRecipient().getId();
        // 먼저 물어본다. 대부분의 재전달을 예외 없이 걸러낸다.
        if (notificationRepository.existsByRecipientIdAndTradeId(recipientId, tradeId)) {
            return;
        }
        try {
            notificationRepository.saveAndFlush(notification);
        } catch (DuplicateKeyException e) {
            // 위 확인과 insert 사이에 다른 컨슈머가 넣었다. 경쟁은 unique 제약이 막았고
            // 결과는 우리가 원하던 그대로다 — 실패로 다루면 DLQ 로 간다.
            //
            // **DataIntegrityViolationException 을 잡으면 안 된다.** 그건 상위 타입이라
            // FK 위반까지 함께 삼킨다 — 존재하지 않는 유저를 가리키는 메시지가
            // "이미 처리됨"으로 조용히 사라진다. DuplicateKeyException 은 unique/PK
            // 위반(SQLState 23505)만 가리키므로, 진짜 데이터 오류는 그대로 올라가
            // 재시도 후 DLQ 로 간다.
            log.debug("알림 중복 — 이미 처리됨. tradeId={}, recipientId={}", tradeId, recipientId);
        }
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
