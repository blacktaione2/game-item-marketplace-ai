package com.gimp.backend.exception;

/** 재고 부족, 낮은 입찰가, 자기 자신과의 거래 등 현재 아이템 상태와 충돌하는 거래 요청. */
public class InvalidTradeRequestException extends RuntimeException {

    public InvalidTradeRequestException(String message) {
        super(message);
    }
}
