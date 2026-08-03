package com.gimp.backend.domain.notification;

import com.gimp.backend.domain.common.BaseTimeEntity;
import com.gimp.backend.domain.tenant.Tenant;
import com.gimp.backend.domain.user.User;
import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.FetchType;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Index;
import jakarta.persistence.JoinColumn;
import jakarta.persistence.ManyToOne;
import jakarta.persistence.Table;
import jakarta.persistence.UniqueConstraint;
import lombok.AccessLevel;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;

/**
 * 체결 후 비동기로 생성되는 알림 (ADR-0030).
 *
 * <p><b>{@code (recipient_id, trade_id)} 에 unique 제약이 걸려 있고, 그게 멱등성 장치다.</b>
 * RabbitMQ 는 at-least-once 라서 같은 메시지가 두 번 올 수 있다 — 커밋은 됐는데 ack 가
 * 실패한 경우가 대표적이다. 그때 두 번째 insert 는 제약에 걸리고, 컨슈머는 그것을
 * <b>실패가 아니라 "이미 처리됨"으로</b> 다룬다. 그렇게 안 하면 멱등성 장치가 오히려
 * 모든 재전달을 DLQ 로 보낸다.
 *
 * <p>거래 하나가 <b>구매자와 판매자 둘</b>에게 알림을 만들므로 {@code trade_id} 단독
 * unique 는 쓸 수 없다.
 *
 * <p><b>이 프로젝트에는 마이그레이션 도구가 없다</b>(Flyway/Liquibase 미사용, Hibernate
 * {@code ddl-auto: update}). 새 테이블은 제약과 함께 생성되므로 이 엔티티는 문제없지만,
 * <b>기존 테이블에 제약을 추가하는 것은 그 방식으로 신뢰할 수 없다</b>는 점은 알고 있어야 한다.
 */
@Entity
@Table(
        name = "notifications",
        uniqueConstraints =
                @UniqueConstraint(
                        name = "uk_notifications_recipient_trade",
                        columnNames = {"recipient_id", "trade_id"}),
        indexes = {
            @Index(name = "idx_notifications_recipient", columnList = "recipient_id, id"),
            @Index(name = "idx_notifications_tenant_id", columnList = "tenant_id")
        })
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class Notification extends BaseTimeEntity {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "tenant_id", nullable = false)
    private Tenant tenant;

    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "recipient_id", nullable = false)
    private User recipient;

    /**
     * 거래 참조를 <b>FK 가 아니라 값</b>으로 들고 있다.
     *
     * <p>알림은 거래의 수명주기에 묶이지 않는다 — 거래가 지워져도 "무엇이 일어났는지"의
     * 기록은 남는 편이 맞다. FK 로 묶으면 {@code seed-demo.sql} 의 거래 삭제가 알림 때문에
     * 막히고, 그 순서를 맞추는 부담이 시드 스크립트로 번진다.
     */
    @Column(name = "trade_id", nullable = false)
    private Long tradeId;

    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 20)
    private NotificationType type;

    @Column(nullable = false, length = 200)
    private String message;

    @Column(nullable = false)
    private boolean read;

    @Builder
    private Notification(
            Tenant tenant, User recipient, Long tradeId, NotificationType type, String message) {
        this.tenant = tenant;
        this.recipient = recipient;
        this.tradeId = tradeId;
        this.type = type;
        this.message = message;
        this.read = false;
    }

    public void markRead() {
        this.read = true;
    }
}
