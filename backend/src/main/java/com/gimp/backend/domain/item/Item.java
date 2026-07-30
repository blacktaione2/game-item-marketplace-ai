package com.gimp.backend.domain.item;

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
import jakarta.persistence.Version;
import java.math.BigDecimal;
import lombok.AccessLevel;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;

/**
 * 거래 대상 아이템(아이템/계정/재화 등). saleType이 AUCTION인 경우에만
 * currentBidPrice/currentBidder가 채워진다.
 */
@Entity
@Table(name = "items", indexes = @Index(name = "idx_items_tenant_id", columnList = "tenant_id"))
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class Item extends BaseTimeEntity {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "tenant_id", nullable = false)
    private Tenant tenant;

    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "seller_id", nullable = false)
    private User seller;

    @Column(nullable = false, length = 200)
    private String name;

    @Column(columnDefinition = "TEXT")
    private String description;

    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 20)
    private SaleType saleType;

    /** FIXED_PRICE: 구매가. AUCTION: 시작가(입찰 시작 기준가). */
    @Column(nullable = false, precision = 19, scale = 2)
    private BigDecimal price;

    @Column(precision = 19, scale = 2)
    private BigDecimal currentBidPrice;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "current_bidder_id")
    private User currentBidder;

    @Column(nullable = false)
    private Integer stock;

    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 20)
    private ItemStatus status;

    /** Redis 분산 락과 별개로, 동시 수정에 대한 최후 방어선(낙관적 락). */
    @Version
    private Long version;

    @Builder
    private Item(
            Tenant tenant,
            User seller,
            String name,
            String description,
            SaleType saleType,
            BigDecimal price,
            Integer stock) {
        this.tenant = tenant;
        this.seller = seller;
        this.name = name;
        this.description = description;
        this.saleType = saleType;
        this.price = price;
        this.stock = stock;
        this.status = ItemStatus.ON_SALE;
    }

    public void updateInfo(String name, String description, BigDecimal price) {
        this.name = name;
        this.description = description;
        this.price = price;
    }

    public void close() {
        this.status = ItemStatus.CLOSED;
    }

    public boolean isOnSale() {
        return this.status == ItemStatus.ON_SALE;
    }

    public void decreaseStock(int quantity) {
        if (this.stock < quantity) {
            throw new IllegalStateException("재고가 부족합니다.");
        }
        this.stock -= quantity;
        if (this.stock == 0) {
            this.status = ItemStatus.SOLD_OUT;
        }
    }

    public void placeBid(BigDecimal bidPrice, User bidder) {
        this.currentBidPrice = bidPrice;
        this.currentBidder = bidder;
    }

    /** 현재 낙찰 대상 금액: 입찰 이력이 있으면 최고 입찰가, 없으면 시작가. */
    public BigDecimal minimumAcceptableBid() {
        return this.currentBidPrice != null ? this.currentBidPrice : this.price;
    }
}
