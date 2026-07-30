package com.gimp.backend.dto.trade;

import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotNull;

public record PurchaseRequest(@NotNull @Min(1) Integer quantity) {}
