package com.gimp.backend.ratelimit;

/**
 * 한도를 거는 대상. <b>메트릭 라벨로 나가므로 값이 유한해야 한다.</b>
 *
 * <p>키를 무엇으로 잡는지가 여기서 갈린다 — {@code DEMO_TOKEN}만 IP를 쓴다. 토큰을 받으러 오는
 * 경로라 아직 신원이 없기 때문이고, 이번 라운드에서 <b>유일한 예외</b>다.
 */
public enum RateLimitScope {
    /** 토큰 발급. 인증 이전이라 IP로 센다. */
    DEMO_TOKEN,
    /** 구매·입찰. 검증된 사용자로 센다. */
    TRADE;

    public String tag() {
        return name().toLowerCase();
    }
}
