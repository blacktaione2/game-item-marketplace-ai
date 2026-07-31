package com.gimp.backend.security;

import com.gimp.backend.domain.user.UserRole;
import org.springframework.security.oauth2.jwt.Jwt;

/**
 * 검증된 토큰에서 뽑아낸 행위자.
 *
 * <p>예전에는 {@code X-Tenant-Id} / {@code X-User-Id} 헤더가 이 역할을 했는데, 헤더는 **누구나 아무 값이나
 * 보낼 수 있다.** 멀티테넌시가 이 프로젝트의 간판인데 테넌트를 자칭하게 두면 격리가 성립하지 않는다. 그래서 두 값은
 * 이제 서명된 클레임에서만 온다(ADR-0023).
 *
 * <p>{@code tenantCode}까지 같이 싣는 이유는 AI 서버가 문자열 코드({@code "nexon"})로 ES 인덱스를 고르기
 * 때문이다. 예전엔 프론트가 두 표기의 차이를 흡수했는데, 이제 발급 시점에 한 번 해소하고 **출처를 토큰 하나로**
 * 만든다.
 */
public record Actor(Long userId, Long tenantId, String tenantCode, UserRole role) {

    public static Actor from(Jwt jwt) {
        return new Actor(
                Long.valueOf(jwt.getSubject()),
                jwt.getClaim(Claims.TENANT_ID),
                jwt.getClaimAsString(Claims.TENANT_CODE),
                UserRole.valueOf(jwt.getClaimAsString(Claims.ROLE)));
    }

    public boolean isAdmin() {
        return role == UserRole.ADMIN;
    }
}
