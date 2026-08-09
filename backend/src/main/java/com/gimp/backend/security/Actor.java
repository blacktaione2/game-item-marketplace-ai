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
 *
 * <p><b>{@code role} 은 백엔드에서 인가에 쓰이지 않는다 — 그건 누락이 아니다.</b> 역할 인가가 필요한 대상은
 * GM 이상거래 검토 큐 하나뿐이고 그건 AI 서버에 있다({@code require_admin}). ADR-0023 이 *"역할 인가는
 * 백엔드에 대상이 없다. 없는 대상을 만들지 않는다"* 고 그렇게 정했다.
 *
 * <p>그런데도 이 필드가 남는 이유는 <b>클레임 계약의 일부</b>이기 때문이다. 아래 {@code valueOf} 가 파싱
 * 시점에 값을 강제하므로, {@code role} 이 없거나 이상한 토큰은 여기서 걸린다. 필드를 지우면 "백엔드는 role 을
 * 모른다"가 되어 ADR-0046 이 필수 클레임 검증을 강화한 방향과 어긋난다.
 *
 * <p>같은 이유로 {@code isAdmin()} 은 <b>지웠다</b>(ADR-0047). 필드는 계약을 표현하지만 그 메서드는
 * <b>동작을 약속한다</b> — "백엔드가 역할로 인가한다" 는 사실이 아니고, 호출자도 없었다. 죽은 코드를 남길
 * 거면 왜 남기는지를 적어야 하고, 적을 이유가 없으면 지우는 게 맞다.
 */
public record Actor(Long userId, Long tenantId, String tenantCode, UserRole role) {

    public static Actor from(Jwt jwt) {
        return new Actor(
                Long.valueOf(jwt.getSubject()),
                jwt.getClaim(Claims.TENANT_ID),
                jwt.getClaimAsString(Claims.TENANT_CODE),
                UserRole.valueOf(jwt.getClaimAsString(Claims.ROLE)));
    }
}
