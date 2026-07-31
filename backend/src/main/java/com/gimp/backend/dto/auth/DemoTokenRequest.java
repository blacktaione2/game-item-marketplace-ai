package com.gimp.backend.dto.auth;

import jakarta.validation.constraints.NotNull;

public record DemoTokenRequest(@NotNull(message = "userId는 필수입니다") Long userId) {}
