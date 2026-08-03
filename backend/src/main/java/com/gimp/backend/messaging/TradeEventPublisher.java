package com.gimp.backend.messaging;

import com.gimp.backend.config.RabbitConfig;
import com.gimp.backend.event.TradeCompletedEvent;
import io.micrometer.core.instrument.MeterRegistry;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.stereotype.Component;
import org.springframework.transaction.event.TransactionPhase;
import org.springframework.transaction.event.TransactionalEventListener;

/**
 * 체결 이벤트를 RabbitMQ 로 내보낸다 (ADR-0030).
 *
 * <h2>왜 {@code AFTER_COMMIT} 인가 — 이 라운드의 핵심</h2>
 *
 * 트랜잭션 <b>안에서</b> 발행하면 두 가지가 깨진다.
 *
 * <ul>
 *   <li><b>롤백돼도 메시지는 나간다.</b> 재고 부족으로 거절된 구매의 알림이 생긴다 —
 *       존재하지 않는 거래를 알리는 셈이다
 *   <li><b>컨슈머가 커밋 전 상태를 본다.</b> 소비가 커밋보다 빠르면 거래를 못 찾는다.
 *       메시지를 자족적으로 만들어 조회를 없앴어도, 롤백 문제는 그대로 남는다
 * </ul>
 *
 * {@code TransactionPhase.AFTER_COMMIT} 은 커밋이 <b>성공한 뒤에만</b> 이 메서드를 부른다.
 * 롤백되면 아예 호출되지 않는다. tests 의 "롤백된 거래는 알림을 만들지 않는다"가 이것을 고정한다.
 *
 * <h2>발행 실패는 거래를 실패시키지 않는다 (fail-open)</h2>
 *
 * 알림은 부가 기능이다. 브로커가 죽었다고 구매가 실패하면 안 된다. 이 저장소의 기존 정책과
 * 같은 결이다 — AI 리미터는 fail-open(비용 방어), 구매 락은 fail-closed(정확성).
 *
 * <p><b>대가는 유실이다.</b> 브로커가 죽은 동안의 알림은 사라진다. 진짜 해결은 outbox
 * 패턴(같은 트랜잭션에 이벤트를 저장하고 별도 프로세스가 발행)인데 범위가 커서 등록만 했다.
 *
 * <p>그리고 이 메서드는 <b>커밋 직후 같은 스레드</b>에서 돈다 — 즉 응답 전에. 브로커가
 * 죽어 있을 때 연결 시도가 길면 <b>구매가 성공은 하는데 느려진다.</b> 그래서
 * {@code spring.rabbitmq.connection-timeout} 을 2초로 낮추고 재시도를 껐다.
 * 그게 fail-open 의 지연 상한이고, 판정선도 그 값에서 도출했다.
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class TradeEventPublisher {

    private final RabbitTemplate rabbitTemplate;
    private final MeterRegistry meterRegistry;

    @TransactionalEventListener(phase = TransactionPhase.AFTER_COMMIT)
    public void publish(TradeCompletedEvent event) {
        try {
            rabbitTemplate.convertAndSend(RabbitConfig.EXCHANGE, RabbitConfig.ROUTING_KEY, event);
            meterRegistry.counter("trade.event.published", "outcome", "ok").increment();
        } catch (Exception e) {
            // 여기서 예외를 밖으로 던지면 이미 커밋된 거래의 응답이 500 이 된다.
            // 거래는 성립했고 알림만 못 보낸 것이므로 삼킨다.
            meterRegistry.counter("trade.event.published", "outcome", "failed").increment();
            log.warn("체결 이벤트 발행 실패 — 거래는 정상 처리됨. tradeId={}", event.tradeId(), e);
        }
    }
}
