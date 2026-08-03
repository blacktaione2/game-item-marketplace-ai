package com.gimp.backend.dto.auth;

import jakarta.validation.constraints.NotBlank;

/** 로그인 (ADR-0031). userId 가 아니라 username 을 받는다 — 신원을 요청이 주장하지 않는다. */
public record LoginRequest(@NotBlank String username, @NotBlank String password) {}
