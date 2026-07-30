package com.gimp.backend.domain.trade;

public enum TradeStatus {
    /** PURCHASE: 결제 완료. BID: 현재 최고 입찰. */
    COMPLETED,
    ACTIVE,
    /** 이후 다른 유저가 더 높은 금액으로 입찰하여 밀려난 BID 건. */
    OUTBID,
    CANCELLED
}
