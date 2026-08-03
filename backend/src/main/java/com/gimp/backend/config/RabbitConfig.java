package com.gimp.backend.config;

import org.springframework.amqp.core.Binding;
import org.springframework.amqp.core.BindingBuilder;
import org.springframework.amqp.core.DirectExchange;
import org.springframework.amqp.core.Queue;
import org.springframework.amqp.core.QueueBuilder;
import org.springframework.amqp.rabbit.connection.ConnectionFactory;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.amqp.support.converter.JacksonJsonMessageConverter;
import org.springframework.amqp.support.converter.MessageConverter;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * 체결 후 알림 큐 (ADR-0030).
 *
 * <p>거래 처리 자체는 큐로 보내지 않는다 — 그러면 API 가 202 를 반환하게 되고, ADR-0020 이
 * 실동시성에서 검증한 {@code 재고 감소분 == 성공 응답 수} 단언이 응답 시점에 성립하지 않게
 * 된다. 큐가 맡는 것은 계획서의 4단계(체결 후 알림)뿐이다.
 */
@Configuration
public class RabbitConfig {

    public static final String EXCHANGE = "gimp.trade";
    public static final String ROUTING_KEY = "trade.completed";
    public static final String QUEUE = "gimp.trade.completed";

    /** 재시도를 소진한 메시지가 가는 곳. 여기 쌓이면 사람이 봐야 하는 신호다. */
    public static final String DLQ_EXCHANGE = "gimp.trade.dlx";
    public static final String DLQ = "gimp.trade.completed.dlq";

    @Bean
    DirectExchange tradeExchange() {
        return new DirectExchange(EXCHANGE, true, false);
    }

    @Bean
    DirectExchange tradeDlxExchange() {
        return new DirectExchange(DLQ_EXCHANGE, true, false);
    }

    /**
     * 소비에 실패하면 브로커가 <b>무한히 재전달한다.</b> 그래서 죽은 편지 경로를 큐 정의에
     * 박아둔다 — 리스너 재시도(application.yml)가 소진되면 여기로 간다.
     */
    @Bean
    Queue tradeCompletedQueue() {
        return QueueBuilder.durable(QUEUE)
                .deadLetterExchange(DLQ_EXCHANGE)
                .deadLetterRoutingKey(ROUTING_KEY)
                .build();
    }

    @Bean
    Queue tradeCompletedDlq() {
        return QueueBuilder.durable(DLQ).build();
    }

    @Bean
    Binding tradeBinding() {
        return BindingBuilder.bind(tradeCompletedQueue()).to(tradeExchange()).with(ROUTING_KEY);
    }

    @Bean
    Binding dlqBinding() {
        return BindingBuilder.bind(tradeCompletedDlq()).to(tradeDlxExchange()).with(ROUTING_KEY);
    }

    /**
     * 이벤트를 JSON 으로 싣는다 — 자바 직렬화는 스키마가 클래스에 묶여 이식성이 없다.
     *
     * <p>{@code Jackson2JsonMessageConverter} 가 아니라 {@code JacksonJsonMessageConverter} 다.
     * Spring AMQP 4(Boot 4)에서 Jackson 3 기반으로 바뀌면서 앞의 것은 <b>제거 예정</b>으로
     * 표시됐다 — 경고를 남겨두면 다음 업그레이드에서 깨진다.
     */
    @Bean
    MessageConverter jsonMessageConverter() {
        return new JacksonJsonMessageConverter();
    }

    @Bean
    RabbitTemplate rabbitTemplate(ConnectionFactory connectionFactory, MessageConverter converter) {
        RabbitTemplate template = new RabbitTemplate(connectionFactory);
        template.setMessageConverter(converter);
        return template;
    }
}
