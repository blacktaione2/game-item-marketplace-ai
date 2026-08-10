package com.gimp.backend.dto.trade;

import com.gimp.backend.domain.trade.Trade;
import com.gimp.backend.domain.trade.TradeStatus;
import com.gimp.backend.domain.trade.TradeType;
import java.math.BigDecimal;
import com.gimp.backend.domain.common.StoredTime;
import java.time.Instant;

public record TradeResponse(
        Long id,
        Long tenantId,
        Long itemId,
        Long buyerId,
        Long sellerId,
        TradeType tradeType,
        BigDecimal price,
        Integer quantity,
        TradeStatus status,
        Instant createdAt) {

    public static TradeResponse from(Trade trade) {
        return new TradeResponse(
                trade.getId(),
                trade.getTenant().getId(),
                trade.getItem().getId(),
                trade.getBuyer().getId(),
                trade.getSeller().getId(),
                trade.getTradeType(),
                trade.getPrice(),
                trade.getQuantity(),
                trade.getStatus(),
                StoredTime.toInstant(trade.getCreatedAt()));
    }
}
