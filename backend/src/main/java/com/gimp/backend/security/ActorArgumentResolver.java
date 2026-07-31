package com.gimp.backend.security;

import org.springframework.core.MethodParameter;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.oauth2.server.resource.authentication.JwtAuthenticationToken;
import org.springframework.stereotype.Component;
import org.springframework.web.bind.support.WebDataBinderFactory;
import org.springframework.web.context.request.NativeWebRequest;
import org.springframework.web.method.support.HandlerMethodArgumentResolver;
import org.springframework.web.method.support.ModelAndViewContainer;

/**
 * 컨트롤러가 {@code Actor}를 파라미터로 바로 받게 한다.
 *
 * <p>{@code @AuthenticationPrincipal Jwt}를 받아 각 컨트롤러에서 {@code Actor.from(jwt)}를 부르는
 * 방법도 있지만, 그러면 클레임 해석이 8곳에 흩어진다. 헤더 파싱이 흩어져 있던 걸 정리하는 게 이번 작업이므로
 * 같은 실수를 반복하지 않는다.
 *
 * <p>여기까지 왔다는 건 필터체인이 이미 토큰을 검증했다는 뜻이다 — 인증이 필요한 경로는 전부
 * {@code authenticated()}라 미인증 요청은 컨트롤러에 도달하지 못한다.
 */
@Component
public class ActorArgumentResolver implements HandlerMethodArgumentResolver {

    @Override
    public boolean supportsParameter(MethodParameter parameter) {
        return Actor.class.equals(parameter.getParameterType());
    }

    @Override
    public Object resolveArgument(
            MethodParameter parameter,
            ModelAndViewContainer mavContainer,
            NativeWebRequest webRequest,
            WebDataBinderFactory binderFactory) {
        Authentication authentication = SecurityContextHolder.getContext().getAuthentication();
        if (authentication instanceof JwtAuthenticationToken token) {
            return Actor.from(token.getToken());
        }
        // 필터체인이 통과시켰는데 JWT가 아니라면 설정이 어긋난 것이다. 조용히 null을 넘기면
        // 그다음에 NPE로 엉뚱한 곳에서 터진다.
        throw new IllegalStateException(
                "인증된 요청인데 JWT 토큰이 없습니다. SecurityConfig의 경로 설정을 확인하세요.");
    }
}
