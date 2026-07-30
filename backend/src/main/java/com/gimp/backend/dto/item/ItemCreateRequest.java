package com.gimp.backend.dto.item;

import com.gimp.backend.domain.item.SaleType;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Positive;
import jakarta.validation.constraints.PositiveOrZero;
import java.math.BigDecimal;

public record ItemCreateRequest(
        @NotBlank String name,
        String description,
        @NotNull SaleType saleType,
        @NotNull @Positive BigDecimal price,
        @NotNull @PositiveOrZero Integer stock) {}
