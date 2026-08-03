package com.gimp.backend.ratelimit;

/**
 * 한도를 거는 대상. <b>메트릭 라벨로 나가므로 값이 유한해야 한다.</b>
 *
 * <p>키를 무엇으로 잡는지가 여기서 갈린다 — {@code LOGIN}만 IP를 쓴다. 아직 신원이 없기
 * 때문이고, 유일한 예외다. 그래서 <b>클라이언트 IP를 어떻게 알아내는가</b>가 이 열거형에
 * 딸린 진짜 문제이며, 프록시 뒤에서는 그게 자명하지 않다(ADR-0031).
 */
public enum RateLimitScope {
    /** 로그인. 인증 이전이라 IP로 센다. */
    LOGIN,
    /** 구매·입찰. 검증된 사용자로 센다. */
    TRADE;

    public String tag() {
        return name().toLowerCase();
    }
}
