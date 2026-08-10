package com.gimp.backend.repository;

import com.gimp.backend.domain.notification.Notification;
import java.util.List;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.transaction.annotation.Transactional;

/**
 * 알림 조회/갱신.
 *
 * <p><b>조회 계열은 테넌트를 조건에 명시한다</b> — {@code TradeRepository.findMine} 이 이미
 * 그 이유를 적어둔 정책이다: <i>"사용자는 한 테넌트에만 속하므로 userId 만으로도 결과는 같지만,
 * 격리를 사용자-테넌트 관계에 의존시키면 그 관계가 바뀌는 날 조용히 샌다."</i> 이 파일은 그
 * 정책을 이어받지 않은 채로 남아 있었다 (ADR-0057).
 *
 * <p>실제로 새는 상태는 아니었다 — user id 는 전역 유일이라 {@code recipientId} 하나가 테넌트를
 * 결정한다. 즉 <b>고친 것은 결함이 아니라 그 정책이 이웃에게 안 건너간 것</b>이고, 이 저장소가
 * 반복해서 겪은 모양이다: <i>한쪽이 선언한 설정을 이웃이 안 하고 있으면 그건 결정이 아니라
 * 누락이다.</i>
 */
public interface NotificationRepository extends JpaRepository<Notification, Long> {

    List<Notification> findByTenantIdAndRecipientIdOrderByIdDesc(
            Long tenantId, Long recipientId, Pageable pageable);

    long countByTenantIdAndRecipientIdAndReadFalse(Long tenantId, Long recipientId);

    /**
     * 멱등성 확인용 — 컨슈머가 재전달을 만났을 때 이미 처리했는지 본다.
     *
     * <p><b>여기에는 테넌트를 더하지 않는다.</b> 위 조회 계열과 목적이 다르다 — 이 검사는
     * 격리가 아니라 <b>유니크 인덱스 {@code (recipient_id, trade_id)} 를 그대로 비추는 것</b>이
     * 일이다. 조건을 하나 더 넣으면 사전확인이 제약보다 좁아지고, 그 틈에 들어온 재전달은
     * 사전확인을 통과해 flush 에서 터진다(= 트랜잭션 rollback-only). <b>사전확인은 제약과
     * 같은 열을 봐야 한다.</b>
     */
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
    @Query("""
            update Notification n set n.read = true
            where n.tenant.id = :tenantId
              and n.recipient.id = :recipientId
              and n.read = false
            """)
    int markAllRead(@Param("tenantId") Long tenantId, @Param("recipientId") Long recipientId);
}
