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
 * 거래 기록. PURCHASE는 생성 즉시 COMPLETED, BID는 ACTIVE/OUTBID 사이에서만 갱신된다.
 *
 * <p><b>낙찰 처리는 범위 밖이다</b> (ADR-0047). 경매는 입찰을 받고 최고가를 갱신하는
 * 데까지이고, 마감·정산·낙찰 알림은 없다 — 마감 시각 컬럼도 스케줄러도 없다. 이건
 * 미완성이 아니라 결정이고, 근거는 ADR-0002의 정정 블록에 있다.
 *
 * <p>그래서 {@code markWon()} 을 <b>지웠다.</b> 호출자가 없는 것보다 나쁜 게, 그
 * 메서드가 있으면 "낙찰이 구현돼 있다"고 읽힌다는 점이다 — 화면은 마감을 약속하지
 * 않으므로 <b>이 저장소에서 그 잘못된 신호를 내던 유일한 자리</b>였다. 나중에 정말
 * 구현한다면 {@code COMPLETED} 가 아니라 {@code WON} 을 새로 만들어야 하므로(재고·정산이
 * 구매와 갈린다) 껍데기를 남겨둬도 재사용되지 않는다.
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
}
