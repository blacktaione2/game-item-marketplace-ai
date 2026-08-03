package com.gimp.backend.dto.auth;

import jakarta.validation.constraints.NotBlank;

/**
 * 로그인 (ADR-0031, 테넌트는 ADR-0034).
 *
 * <p>userId 가 아니라 username 을 받는다 — 신원을 요청이 주장하지 않는다.
 *
 * <p><b>{@code tenantCode} 가 함께 온다.</b> 아이디는 테넌트 안에서만 유일하므로
 * (제약이 {@code (tenant_id, username)} 이다) 아이디 하나로는 계정이 특정되지 않는다.
 * 자격증명이 <b>테넌트 + 아이디 + 비밀번호</b> 세 쪽인 셈이다.
 *
 * <p>이게 "요청이 신원을 주장하지 않는다"(ADR-0022·0023)와 충돌하지 않는 이유:
 * <b>로그인은 자격증명을 제시하는 자리 자체다.</b> 발급 이후에는 테넌트가 예전처럼
 * 토큰 클레임에서만 오고, 요청 본문의 {@code tenant_code} 는 여전히 어디에도 없다.
 */
public record LoginRequest(
        @NotBlank String tenantCode, @NotBlank String username, @NotBlank String password) {}
