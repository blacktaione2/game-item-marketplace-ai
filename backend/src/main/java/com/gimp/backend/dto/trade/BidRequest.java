package com.gimp.backend.dto.trade;

import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Positive;
import java.math.BigDecimal;

public record BidRequest(@NotNull @Positive BigDecimal bidPrice) {}
