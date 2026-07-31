package com.gimp.backend.exception;

import lombok.Getter;

/**
 * 요청 한도 초과 (ADR-0024).
 *
 * <p>{@code scope}는 메트릭 라벨로 나가므로 <b>값의 종류가 유한해야 한다</b> — 경로 패턴 단위지 사용자
 * 단위가 아니다(ADR-0019의 카디널리티 규칙).
 */
@Getter
public class RateLimitExceededException extends RuntimeException {

    private final String scope;
    private final long retryAfterSeconds;

    public RateLimitExceededException(String scope, long retryAfterSeconds) {
        super("요청이 너무 잦습니다. " + retryAfterSeconds + "초 후 다시 시도해주세요.");
        this.scope = scope;
        this.retryAfterSeconds = retryAfterSeconds;
    }
}
