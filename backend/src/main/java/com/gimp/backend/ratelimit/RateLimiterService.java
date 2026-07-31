package com.gimp.backend.ratelimit;

import java.time.Duration;
import org.redisson.api.RRateLimiter;
import org.redisson.api.RateType;
import org.redisson.api.RedissonClient;
import org.springframework.stereotype.Service;

/**
 * Redisson {@code RRateLimiter} 기반 한도 검사 (ADR-0024).
 *
 * <p><b>새 의존성이 없다.</b> {@code org.redisson:redisson}은 분산 락 때문에 이미 있고, 상태가
 * Redis에 있으므로 인스턴스를 늘려도 한도가 합산된다. 토큰 버킷이라 AI 서버 쪽 고정 윈도우보다
 * 경계 동작이 낫다 — 두 서버가 다른 방식을 쓰는 것은 각자 <b>이미 가진 것</b>을 쓰기 때문이다.
 */
@Service
public class RateLimiterService {

    private final RedissonClient redisson;

    public RateLimiterService(RedissonClient redisson) {
        this.redisson = redisson;
    }

    /**
     * @param scope 한도 대상 (메트릭 라벨)
     * @param subject 세는 단위 — 사용자 id 또는 IP
     * @param permits 창당 허용 횟수
     * @param windowSeconds 창 길이
     * @return 통과하면 true
     */
    public boolean tryAcquire(RateLimitScope scope, String subject, int permits, int windowSeconds) {
        Duration window = Duration.ofSeconds(windowSeconds);

        // **설정값을 키에 넣는다.** RRateLimiter는 rate를 Redis에 저장하고 trySetRate는 이미
        // 설정돼 있으면 아무것도 하지 않는다. 키가 그대로면 한도를 바꿔도 옛 값이 계속 산다 —
        // 부하테스트용으로 올렸다 되돌렸을 때 조용히 안 돌아오는 종류의 함정이다.
        String key = "ratelimit:%s:%d/%ds:%s".formatted(scope.tag(), permits, windowSeconds, subject);

        RRateLimiter limiter = redisson.getRateLimiter(key);
        // 4번째 인자가 keepAlive다. 사용자·IP마다 키가 생기므로 안 쓰면 사라져야 한다.
        limiter.trySetRate(RateType.OVERALL, permits, window, window.multipliedBy(10));
        return limiter.tryAcquire();
    }
}
