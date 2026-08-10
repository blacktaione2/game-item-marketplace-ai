package com.gimp.backend.dto.notification;

import com.gimp.backend.domain.notification.Notification;
import com.gimp.backend.domain.notification.NotificationType;
import com.gimp.backend.domain.common.StoredTime;
import java.time.Instant;

public record NotificationResponse(
        Long id,
        Long tradeId,
        NotificationType type,
        String message,
        boolean read,
        Instant createdAt) {

    public static NotificationResponse from(Notification n) {
        return new NotificationResponse(
                n.getId(),
                n.getTradeId(),
                n.getType(),
                n.getMessage(),
                n.isRead(),
                StoredTime.toInstant(n.getCreatedAt()));
    }
}
