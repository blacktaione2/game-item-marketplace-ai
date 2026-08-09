package com.gimp.backend.dto.trade;

import com.gimp.backend.domain.item.Item;
import jakarta.validation.constraints.DecimalMax;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Positive;
import java.math.BigDecimal;

/**
 * 입찰가.
 *
 * <p>상한은 {@code current_bid_price} 컬럼이 담을 수 있는 값이다 (ADR-0035). 없으면
 * {@code 10^17} 이상의 입찰이 검증을 통과하고 UPDATE 시점에 {@code numeric field
 * overflow} 로 터져 <b>500</b> 이 된다 — 잘못된 입력은 400 이어야 한다.
 *
 * <p>아이템 등록가만 묶고 입찰가를 열어두면 같은 결함이 경로 하나로 남는다. 실제로
 * 금액 필드 셋이 나란히 상한 없이 있었다.
 */
public record BidRequest(
        @NotNull @Positive @DecimalMax(Item.MAX_PRICE) BigDecimal bidPrice) {}
