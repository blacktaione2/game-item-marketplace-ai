package com.gimp.backend.config;

import com.gimp.backend.ratelimit.RateLimitInterceptor;
import com.gimp.backend.security.ActorArgumentResolver;
import java.util.List;
import lombok.RequiredArgsConstructor;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.method.support.HandlerMethodArgumentResolver;
import org.springframework.web.servlet.config.annotation.InterceptorRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

@Configuration
@RequiredArgsConstructor
public class WebConfig implements WebMvcConfigurer {

    private final ActorArgumentResolver actorArgumentResolver;
    private final RateLimitInterceptor rateLimitInterceptor;

    @Override
    public void addArgumentResolvers(List<HandlerMethodArgumentResolver> resolvers) {
        resolvers.add(actorArgumentResolver);
    }

    /**
     * 한도를 거는 경로를 <b>여기 한 곳에 명시</b>한다 (ADR-0024).
     *
     * <ul>
     *   <li>{@code /api/auth/**} — 인증 없이 열려 있는 유일한 경로다. 여기가 무제한이면 나머지
     *       한도는 "토큰을 새로 받아 우회"가 가능해진다
     *   <li>구매·입찰 — 남용 방지. 조회는 걸지 않는다(싸고, 걸 근거가 없다)
     * </ul>
     */
    @Override
    public void addInterceptors(InterceptorRegistry registry) {
        registry.addInterceptor(rateLimitInterceptor)
                .addPathPatterns("/api/auth/**", "/api/items/*/purchase", "/api/items/*/bids");
    }
}
