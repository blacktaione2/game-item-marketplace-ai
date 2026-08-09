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

    /**
     * 금액 컬럼이 담을 수 있는 최댓값. <b>DTO 의 {@code @DecimalMax} 가 이 값을 쓴다.</b>
     *
     * <p>{@code numeric(19,2)} 는 절댓값이 {@code 10^17} 미만이어야 한다(PostgreSQL:
     * "A field with precision 19, scale 2 must round to an absolute value less than
     * 10^17"). 그래서 상한은 소수 둘까지 채운 {@code 99999999999999999.99} 다.
     *
     * <p>여기 두는 이유는 <b>숫자가 컬럼 정의 옆에 있어야 하기 때문</b>이다. 세 DTO 에
     * 같은 리터럴을 적으면 {@code precision} 을 고칠 때 한 곳만 바뀐다 — 이 저장소가
     * 반복해서 겪은 결함이다.
     */
    public static final String MAX_PRICE = "99999999999999999.99";

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

    /**
     * 다음 입찰이 넘어서야 하는 금액: 입찰 이력이 있으면 최고 입찰가, 없으면 시작가.
     *
     * <p>"낙찰 대상"이라고 쓰지 않는다 — <b>낙찰 처리가 없다</b>(ADR-0047, {@link
     * com.gimp.backend.domain.trade.Trade} 참고). 이 값은 최고가일 뿐 아무것도
     * 확정하지 않는다.
     */
    public BigDecimal minimumAcceptableBid() {
        return this.currentBidPrice != null ? this.currentBidPrice : this.price;
    }
}
