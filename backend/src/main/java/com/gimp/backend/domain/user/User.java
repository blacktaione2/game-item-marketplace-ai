package com.gimp.backend.domain.user;

import com.gimp.backend.domain.common.BaseTimeEntity;
import com.gimp.backend.domain.tenant.Tenant;
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
 * 유저. 하나의 유저 계정은 하나의 테넌트(게임사 거래소)에 속한다.
 */
@Entity
@Table(
        name = "users",
        indexes = @Index(name = "idx_users_tenant_id", columnList = "tenant_id"),
        uniqueConstraints =
                @UniqueConstraint(name = "uk_users_tenant_username", columnNames = {"tenant_id", "username"}))
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class User extends BaseTimeEntity {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "tenant_id", nullable = false)
    private Tenant tenant;

    @Column(nullable = false, length = 50)
    private String username;

    @Column(nullable = false, length = 100)
    private String email;

    @Column(nullable = false)
    private String passwordHash;

    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 20)
    private UserRole role;

    @Builder
    private User(Tenant tenant, String username, String email, String passwordHash, UserRole role) {
        this.tenant = tenant;
        this.username = username;
        this.email = email;
        this.passwordHash = passwordHash;
        this.role = role == null ? UserRole.USER : role;
    }

    /** 기동 시 데모 비밀번호를 주입할 때만 쓴다 (ADR-0031, DemoAccountInitializer). */
    public void changePassword(String encodedPassword) {
        this.passwordHash = encodedPassword;
    }
}
