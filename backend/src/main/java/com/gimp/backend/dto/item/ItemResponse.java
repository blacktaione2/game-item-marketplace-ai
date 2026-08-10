package com.gimp.backend.dto.item;

import com.gimp.backend.domain.item.Item;
import com.gimp.backend.domain.item.ItemStatus;
import com.gimp.backend.domain.item.SaleType;
import java.math.BigDecimal;
import com.gimp.backend.domain.common.StoredTime;
import java.time.Instant;

public record ItemResponse(
        Long id,
        Long tenantId,
        Long sellerId,
        String sellerUsername,
        String name,
        String description,
        SaleType saleType,
        BigDecimal price,
        BigDecimal currentBidPrice,
        Long currentBidderId,
        Integer stock,
        ItemStatus status,
        Instant createdAt,
        Instant updatedAt) {

    public static ItemResponse from(Item item) {
        return new ItemResponse(
                item.getId(),
                item.getTenant().getId(),
                item.getSeller().getId(),
                item.getSeller().getUsername(),
                item.getName(),
                item.getDescription(),
                item.getSaleType(),
                item.getPrice(),
                item.getCurrentBidPrice(),
                item.getCurrentBidder() != null ? item.getCurrentBidder().getId() : null,
                item.getStock(),
                item.getStatus(),
                StoredTime.toInstant(item.getCreatedAt()),
                StoredTime.toInstant(item.getUpdatedAt()));
    }
}
