package com.gimp.backend.dto.item;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Positive;
import jakarta.validation.constraints.Size;
import java.math.BigDecimal;

/**
 * 재고/판매상태는 구매·입찰 로직을 통해서만 바뀌므로 수정 대상에서 제외한다.
 *
 * <p>길이 상한은 {@link ItemCreateRequest} 와 <b>같아야 한다</b> (ADR-0035). 등록만
 * 막고 수정을 열어두면 같은 결함이 경로 하나로 남는다 — 생성 시 200자로 막아도
 * 수정으로 300자를 넣으면 그대로 500 이다.
 */
public record ItemUpdateRequest(
        @NotBlank @Size(max = 200) String name,
        @Size(max = 5000) String description,
        @NotNull @Positive BigDecimal price) {}
