package com.gimp.backend.repository;

import com.gimp.backend.domain.notification.Notification;
import java.util.List;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.transaction.annotation.Transactional;

public interface NotificationRepository extends JpaRepository<Notification, Long> {

    List<Notification> findByRecipientIdOrderByIdDesc(Long recipientId, Pageable pageable);

    long countByRecipientIdAndReadFalse(Long recipientId);

    /** 멱등성 확인용 — 컨슈머가 재전달을 만났을 때 이미 처리했는지 본다. */
    boolean existsByRecipientIdAndTradeId(Long recipientId, Long tradeId);

    /**
     * 안 읽은 알림을 전부 읽음으로 바꾼다.
     *
     * <p>엔티티를 하나씩 불러 {@code markRead()} 를 돌리지 않고 벌크 update 를 쓴다 — 목록 상한이
     * 20건인 것과 달리 <b>안 읽은 알림에는 상한이 없어서</b> 로드하면 몇 건이 올지 알 수 없다.
     *
     * <p>{@code clearAutomatically} 로 영속성 컨텍스트를 비운다. 벌크 update 는 DB 를 직접 치므로,
     * 같은 트랜잭션에 이미 올라온 엔티티가 있으면 {@code read=false} 인 옛 상태가 그대로 남는다.
     */
    @Modifying(clearAutomatically = true)
    @Transactional
    @Query("update Notification n set n.read = true where n.recipient.id = :recipientId and n.read = false")
    int markAllRead(@Param("recipientId") Long recipientId);
}
