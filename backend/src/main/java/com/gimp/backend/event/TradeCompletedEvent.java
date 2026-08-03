package com.gimp.backend.event;

import com.gimp.backend.domain.trade.TradeType;
import java.math.BigDecimal;

/**
 * 체결 직후 발행되는 도메인 이벤트 (ADR-0030).
 *
 * <p><b>필요한 값을 전부 실어 보낸다. id 만 보내지 않는다.</b> 컨슈머가 id 로 거래를 다시
 * 조회하면 (1) DB 왕복이 늘고 (2) 조회 시점의 상태를 보게 되며 (3) 무엇보다 <b>커밋과
 * 소비의 경쟁</b>이 생긴다 — 커밋 전에 소비가 시작되면 조회가 실패한다. 그 경쟁은
 * {@code AFTER_COMMIT} 발행으로 대부분 닫히지만, 자족적인 메시지면 애초에 열리지 않는다.
 *
 * <p>{@code previousBidderId} 는 입찰에서 밀려난 사람이다. 없으면 {@code null} —
 * 첫 입찰이거나 구매다.
 *
 * <p>record 라서 Jackson 이 그대로 직렬화한다. 필드를 추가할 때는 <b>컨슈머가 옛 메시지도
 * 읽을 수 있는지</b> 확인할 것 — 큐에 남아 있던 메시지는 이전 스키마다.
 */
public record TradeCompletedEvent(
        Long tradeId,
        Long tenantId,
        Long itemId,
        String itemName,
        Long buyerId,
        Long sellerId,
        Long previousBidderId,
        TradeType tradeType,
        BigDecimal price,
        Integer quantity) {}
