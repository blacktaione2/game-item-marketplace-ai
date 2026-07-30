package com.gimp.backend.domain.trade;

import com.gimp.backend.domain.common.BaseTimeEntity;
import com.gimp.backend.domain.item.Item;
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
import java.math.BigDecimal;
import lombok.AccessLevel;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;

/**
 * 거래 기록. PURCHASE는 생성 즉시 COMPLETED, BID는 낙찰 전까지 ACTIVE/OUTBID로 갱신된다.
 */
@Entity
@Table(name = "trades", indexes = @Index(name = "idx_trades_tenant_id", columnList = "tenant_id"))
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class Trade extends BaseTimeEntity {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "tenant_id", nullable = false)
    private Tenant tenant;

    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "item_id", nullable = false)
    private Item item;

    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "buyer_id", nullable = false)
    private User buyer;

    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "seller_id", nullable = false)
    private User seller;

    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 20)
    private TradeType tradeType;

    @Column(nullable = false, precision = 19, scale = 2)
    private BigDecimal price;

    @Column(nullable = false)
    private Integer quantity;

    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 20)
    private TradeStatus status;

    @Builder
    private Trade(
            Tenant tenant,
            Item item,
            User buyer,
            User seller,
            TradeType tradeType,
            BigDecimal price,
            Integer quantity,
            TradeStatus status) {
        this.tenant = tenant;
        this.item = item;
        this.buyer = buyer;
        this.seller = seller;
        this.tradeType = tradeType;
        this.price = price;
        this.quantity = quantity;
        this.status = status;
    }

    public void markOutbid() {
        this.status = TradeStatus.OUTBID;
    }

    public void markWon() {
        this.status = TradeStatus.COMPLETED;
    }
}
