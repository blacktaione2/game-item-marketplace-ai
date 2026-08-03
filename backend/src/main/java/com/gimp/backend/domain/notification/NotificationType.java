package com.gimp.backend.domain.notification;

/** 알림 종류. 수신자가 구매자인지 판매자인지에 따라 문구가 달라진다. */
public enum NotificationType {
    /** 구매자에게: 구매가 체결됐다. */
    PURCHASE_COMPLETED,
    /** 판매자에게: 내 아이템이 팔렸다. */
    ITEM_SOLD,
    /** 입찰자에게: 입찰이 등록됐다. */
    BID_PLACED,
    /** 이전 최고 입찰자에게: 더 높은 입찰에 밀렸다. */
    OUTBID
}
