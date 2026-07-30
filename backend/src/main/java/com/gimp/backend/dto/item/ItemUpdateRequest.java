package com.gimp.backend.dto.item;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Positive;
import java.math.BigDecimal;

/** 재고/판매상태는 구매·입찰 로직을 통해서만 바뀌므로 수정 대상에서 제외한다. */
public record ItemUpdateRequest(
        @NotBlank String name, String description, @NotNull @Positive BigDecimal price) {}
