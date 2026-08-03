package com.gimp.backend.repository;

import com.gimp.backend.domain.notification.Notification;
import java.util.List;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;

public interface NotificationRepository extends JpaRepository<Notification, Long> {

    List<Notification> findByRecipientIdOrderByIdDesc(Long recipientId, Pageable pageable);

    long countByRecipientIdAndReadFalse(Long recipientId);

    /** 멱등성 확인용 — 컨슈머가 재전달을 만났을 때 이미 처리했는지 본다. */
    boolean existsByRecipientIdAndTradeId(Long recipientId, Long tradeId);
}
