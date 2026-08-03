package com.gimp.backend.exception;

import io.micrometer.core.instrument.MeterRegistry;
import java.util.HashMap;
import java.util.Map;
import org.springframework.dao.OptimisticLockingFailureException;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

/**
 * 응답 형태를 {@code {"message": ...}} 로 통일하고, 거절을 <b>사유별로</b> 센다.
 *
 * <p>구매/입찰 거절은 셋 다 409로 나가서 상태 코드만으로는 구분되지 않는다. 부하테스트에서
 * "락 경합이 병목인가"를 답하려면 락 타임아웃과 낙관적 락 충돌과 단순 재고 부족을 갈라야
 * 하므로, 여기서 사유를 태그로 남긴다. 핸들러가 이미 사유별로 나뉘어 있어 이 자리가 가장 싸다.
 */
@RestControllerAdvice
public class GlobalExceptionHandler {

    private final MeterRegistry meterRegistry;

    public GlobalExceptionHandler(MeterRegistry meterRegistry) {
        this.meterRegistry = meterRegistry;
    }

    private void countRejection(String reason) {
        meterRegistry.counter("trade.rejection", "reason", reason).increment();
    }

    /**
     * 로그인 실패 (ADR-0031).
     *
     * <p><b>핸들러가 없으면 500 이 나간다.</b> Spring Security 의 예외 변환은 필터 체인에서
     * 던져진 것만 다루는데, 이건 컨트롤러 안쪽(서비스)에서 나온다. 자격증명이 틀렸을 뿐인데
     * 서버 오류로 보고하면 (1) 클라이언트가 재시도를 하고 (2) 로그가 오류로 오염된다.
     *
     * <p>메시지는 <b>아이디와 비밀번호를 구분하지 않는다</b> — 갈리면 사용자 열거가 된다.
     */
    @ExceptionHandler(org.springframework.security.authentication.BadCredentialsException.class)
    public ResponseEntity<ErrorResponse> handleBadCredentials(
            org.springframework.security.authentication.BadCredentialsException e) {
        countRejection("bad_credentials");
        return ResponseEntity.status(HttpStatus.UNAUTHORIZED).body(ErrorResponse.of(e.getMessage()));
    }

    @ExceptionHandler(ResourceNotFoundException.class)
    public ResponseEntity<ErrorResponse> handleNotFound(ResourceNotFoundException e) {
        countRejection("not_found");
        return ResponseEntity.status(HttpStatus.NOT_FOUND).body(ErrorResponse.of(e.getMessage()));
    }

    @ExceptionHandler(InvalidTradeRequestException.class)
    public ResponseEntity<ErrorResponse> handleInvalidTrade(InvalidTradeRequestException e) {
        countRejection("invalid_request");
        return ResponseEntity.status(HttpStatus.CONFLICT).body(ErrorResponse.of(e.getMessage()));
    }

    /** Redis 락이 우회되었을 때의 최후 방어선(@Version)이 실제로 걸린 경우. ADR 0001 참고. */
    @ExceptionHandler(OptimisticLockingFailureException.class)
    public ResponseEntity<ErrorResponse> handleOptimisticLock(OptimisticLockingFailureException e) {
        // 이 값이 0이 아니면 분산 락이 새고 있다는 뜻이라 그 자체로 신호다.
        countRejection("optimistic_conflict");
        return ResponseEntity.status(HttpStatus.CONFLICT)
                .body(ErrorResponse.of("다른 요청과 충돌했습니다. 다시 시도해주세요."));
    }

    @ExceptionHandler(LockAcquisitionException.class)
    public ResponseEntity<ErrorResponse> handleLockAcquisition(LockAcquisitionException e) {
        countRejection("lock_timeout");
        return ResponseEntity.status(HttpStatus.CONFLICT).body(ErrorResponse.of(e.getMessage()));
    }

    /**
     * 한도 초과 (ADR-0024).
     *
     * <p>카운터를 {@code trade.rejection}에 합치지 않은 이유는 이름이 거짓이 되기 때문이다 —
     * 토큰 발급은 거래가 아니다. 계측 <b>지점</b>은 여기 하나로 유지하되 이름은 정직하게 둔다.
     */
    @ExceptionHandler(RateLimitExceededException.class)
    public ResponseEntity<ErrorResponse> handleRateLimit(RateLimitExceededException e) {
        meterRegistry.counter("rate.limited", "scope", e.getScope()).increment();
        return ResponseEntity.status(HttpStatus.TOO_MANY_REQUESTS)
                .header("Retry-After", String.valueOf(e.getRetryAfterSeconds()))
                .body(ErrorResponse.of(e.getMessage()));
    }

    @ExceptionHandler(MethodArgumentNotValidException.class)
    public ResponseEntity<ErrorResponse> handleValidation(MethodArgumentNotValidException e) {
        Map<String, String> fieldErrors = new HashMap<>();
        e.getBindingResult()
                .getFieldErrors()
                .forEach(fe -> fieldErrors.put(fe.getField(), fe.getDefaultMessage()));
        return ResponseEntity.status(HttpStatus.BAD_REQUEST)
                .body(ErrorResponse.of("입력값이 올바르지 않습니다.", fieldErrors));
    }
}
