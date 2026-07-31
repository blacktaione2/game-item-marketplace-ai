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

    @Value("${rate-limit.demo-token.permits}")
    private int demoTokenPermits;

    @Value("${rate-limit.demo-token.window-seconds}")
    private int demoTokenWindowSeconds;

    public RateLimitInterceptor(RateLimiterService rateLimiter) {
        this.rateLimiter = rateLimiter;
    }

    @Override
    public boolean preHandle(HttpServletRequest request, HttpServletResponse response, Object handler) {
        if (!enabled) {
            return true;
        }

        boolean isTokenIssue = request.getRequestURI().startsWith("/api/auth/");
        RateLimitScope scope = isTokenIssue ? RateLimitScope.DEMO_TOKEN : RateLimitScope.TRADE;
        String subject = isTokenIssue ? clientIp(request) : String.valueOf(currentUserId());
        int permits = isTokenIssue ? demoTokenPermits : tradePermits;
        int window = isTokenIssue ? demoTokenWindowSeconds : tradeWindowSeconds;

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

    private String clientIp(HttpServletRequest request) {
        // **X-Forwarded-For를 보지 않는다.** 신뢰할 프록시가 없는 상태에서 그 헤더를 읽으면
        // 누구나 값을 바꿔가며 새 버킷을 받을 수 있다 — 한도를 거는 코드가 스스로 우회로를
        // 만드는 셈이다. 리버스 프록시를 두게 되면 그때 ForwardedHeaderFilter로 신뢰 경계를
        // 세우고 나서 XFF를 읽어야 한다. 순서가 반대면 안 된다.
        return request.getRemoteAddr();
    }
}
