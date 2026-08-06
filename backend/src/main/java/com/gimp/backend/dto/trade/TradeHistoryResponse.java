package com.gimp.backend.dto.trade;

import com.gimp.backend.domain.trade.Trade;
import com.gimp.backend.domain.trade.TradeStatus;
import com.gimp.backend.domain.trade.TradeType;
import com.gimp.backend.domain.user.User;
import java.math.BigDecimal;
import java.time.LocalDateTime;

/**
 * 거래 내역 한 줄.
 *
 * <p>{@link TradeResponse} 와 따로 두는 이유는 <b>보는 사람이 정해져 있기 때문</b>이다. 체결 응답은
 * 방금 만든 거래를 그대로 돌려주면 되지만, 내역은 "내가 산 것인가 판 것인가"와 "상대가 누구인가"를
 * 알아야 읽힌다. 그 둘은 조회하는 사람이 누구냐에 따라 달라지므로 거래 자체의 필드가 아니다.
 *
 * <p>id 대신 이름을 담는 것도 같은 이유다. {@code buyerId: 3} 은 화면에서 아무 뜻이 없다.
 */
public record TradeHistoryResponse(
        Long id,
        Long itemId,
        String itemName,
        TradeType tradeType,
        TradeStatus status,
        BigDecimal price,
        Integer quantity,
        Side side,
        String counterpartyUsername,
        LocalDateTime createdAt) {

    /** 이 거래에서 조회자가 어느 쪽이었나. */
    public enum Side {
        BUY,
        SELL
    }

    public static TradeHistoryResponse from(Trade trade, Long viewerId) {
        boolean bought = trade.getBuyer().getId().equals(viewerId);
        User counterparty = bought ? trade.getSeller() : trade.getBuyer();
        return new TradeHistoryResponse(
                trade.getId(),
                trade.getItem().getId(),
                trade.getItem().getName(),
                trade.getTradeType(),
                trade.getStatus(),
                trade.getPrice(),
                trade.getQuantity(),
                bought ? Side.BUY : Side.SELL,
                counterparty.getUsername(),
                trade.getCreatedAt());
    }
}
