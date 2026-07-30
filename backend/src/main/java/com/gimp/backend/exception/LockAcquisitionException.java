package com.gimp.backend.exception;

/** 동일 아이템에 대한 다른 거래가 처리 중이라 Redis 분산 락을 제한 시간 내에 획득하지 못한 경우. */
public class LockAcquisitionException extends RuntimeException {

    public LockAcquisitionException(String message) {
        super(message);
    }
}
