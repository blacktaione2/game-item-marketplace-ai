package com.gimp.backend.dto.item;

import com.gimp.backend.domain.item.SaleType;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Positive;
import jakarta.validation.constraints.PositiveOrZero;
import jakarta.validation.constraints.Size;
import java.math.BigDecimal;

/**
 * 아이템 등록.
 *
 * <p><b>길이 상한이 DB 제약과 같아야 한다</b> (ADR-0035). {@code name} 컬럼이
 * {@code varchar(200)} 인데 여기에는 {@code @Size} 가 없어서, 201자를 보내면 검증을
 * 통과하고 <b>INSERT 시점에 터져 500</b> 이 나왔다(실측: 300자 → 500, 200자 → 201).
 * 잘못된 입력은 400 이어야 한다 — 500 은 "서버가 고장났다"는 뜻이고 클라이언트가
 * 재시도하게 만든다.
 *
 * <p>{@code description} 은 컬럼이 {@code TEXT} 라 DB 상한이 없다. 그래도 묶는다 —
 * 공개 배포에서 상한 없는 문자열은 저장소를 채우는 경로가 된다. 5,000자는 데모
 * 아이템 설명(최장 60자대)의 넉넉한 배수다.
 */
public record ItemCreateRequest(
        @NotBlank @Size(max = 200) String name,
        @Size(max = 5000) String description,
        @NotNull SaleType saleType,
        @NotNull @Positive BigDecimal price,
        @NotNull @PositiveOrZero Integer stock) {}
