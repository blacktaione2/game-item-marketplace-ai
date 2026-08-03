package com.gimp.backend.ratelimit;

import com.gimp.backend.exception.RateLimitExceededException;
import com.gimp.backend.security.Actor;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.oauth2.server.resource.authentication.JwtAuthenticationToken;
import org.springframework.stereotype.Component;
import org.springframework.web.servlet.HandlerInterceptor;

/**
 * 경로별 한도 적용 (ADR-0024).
 *
 * <p><b>모든 경로에 걸지 않는다.</b> 막아야 할 것은 비용과 남용이고, 조회는 둘 다 아니다.
 * 등록 대상은 {@code WebConfig}에 명시돼 있다.
 *
 * <p>키가 대상마다 다르다 — 거래는 <b>검증된 사용자</b>, 토큰 발급은 <b>IP</b>다. 발급 경로는
 * 인증 이전이라 신원이 없기 때문이고, 이번 라운드에서 유일한 예외다.
 */
@Component
public class RateLimitInterceptor implements HandlerInterceptor {

    private final RateLimiterService rateLimiter;

    @Value("${rate-limit.enabled}")
    private boolean enabled;

    @Value("${rate-limit.trade.permits}")
    private int tradePermits;

    @Value("${rate-limit.trade.window-seconds}")
    private int tradeWindowSeconds;

    @Value("${rate-limit.login.permits}")
    private int loginPermits;

    @Value("${rate-limit.login.window-seconds}")
    private int loginWindowSeconds;

    /**
     * 이 주소들에서 온 요청만 {@code X-Forwarded-For} 를 신뢰한다.
     * 비어 있으면(기본) 헤더를 아예 안 읽는다 — 로컬 실행이 현행과 같아진다.
     */
    @Value("${rate-limit.trusted-proxies:}")
    private java.util.Set<String> trustedProxies = java.util.Set.of();

    public RateLimitInterceptor(RateLimiterService rateLimiter) {
        this.rateLimiter = rateLimiter;
    }

    @Override
    public boolean preHandle(HttpServletRequest request, HttpServletResponse response, Object handler) {
        if (!enabled) {
            return true;
        }

        boolean isLogin = request.getRequestURI().startsWith("/api/auth/");
        RateLimitScope scope = isLogin ? RateLimitScope.LOGIN : RateLimitScope.TRADE;
        String subject = isLogin ? clientIp(request) : String.valueOf(currentUserId());
        int permits = isLogin ? loginPermits : tradePermits;
        int window = isLogin ? loginWindowSeconds : tradeWindowSeconds;

        if (!rateLimiter.tryAcquire(scope, subject, permits, window)) {
            throw new RateLimitExceededException(scope.tag(), window);
        }
        return true;
    }

    private Long currentUserId() {
        Authentication authentication = SecurityContextHolder.getContext().getAuthentication();
        if (authentication instanceof JwtAuthenticationToken token) {
            return Actor.from(token.getToken()).userId();
        }
        // 거래 경로는 authenticated()라 여기 도달할 수 없다. 도달했다면 경로 설정이 어긋난 것이다.
        throw new IllegalStateException("인증된 요청인데 JWT 토큰이 없습니다.");
    }

    /**
     * 클라이언트 IP. <b>신뢰하는 프록시에서 온 요청일 때만</b> {@code X-Forwarded-For} 를 읽는다.
     *
     * <p>ADR-0024 는 이 헤더를 아예 안 읽었다. 근거는 "신뢰할 프록시가 전제되지 않으면
     * 누구나 값을 바꿔 새 버킷을 받는다"였고, 그때는 맞았다. <b>ADR-0029 의 nginx 로 그 전제가
     * 생겼고</b>, 공개 배포에서는 안 읽는 쪽이 오히려 문제가 된다 — 모든 요청의 IP 가 프록시
     * 하나로 합쳐져 한도가 배포 전체 합계가 된다.
     *
     * <p><b>기본값은 빈 목록이다.</b> 즉 설정을 안 하면 예전과 똑같이 XFF 를 무시한다.
     * 잊은 배포가 <b>더 안전한 쪽</b>으로 실패해야 하기 때문이다.
     *
     * <p>맨 마지막 값을 쓴다. XFF 는 {@code 클라이언트, 프록시1, 프록시2} 순으로 쌓이는데,
     * 앞쪽은 클라이언트가 위조해 넣을 수 있고 <b>맨 뒤가 우리 프록시가 붙인 값</b>이다.
     */
    private String clientIp(HttpServletRequest request) {
        String remote = request.getRemoteAddr();
        if (!trustedProxies.contains(remote)) {
            return remote;
        }
        String forwarded = request.getHeader("X-Forwarded-For");
        if (forwarded == null || forwarded.isBlank()) {
            return remote;
        }
        String[] parts = forwarded.split(",");
        return parts[parts.length - 1].trim();
    }
}
