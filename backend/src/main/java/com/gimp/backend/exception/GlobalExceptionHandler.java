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
