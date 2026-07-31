package com.gimp.backend.security;

/**
 * 토큰 클레임 이름.
 *
 * <p>**검증기가 두 벌이다** — 여기(Java)와 {@code ai/app/core/auth.py}(Python). 게이트웨이를 두지 않기로
 * 했으므로(ADR-0023) 두 구현이 갈라지지 않게 이름을 한 곳에 모아둔다. 이 파일을 고치면 파이썬 쪽
 * {@code REQUIRED_CLAIMS}도 같이 고쳐야 한다.
 */
public final class Claims {

    /** 테넌트 숫자 id. 백엔드의 모든 조회가 이걸로 격리된다. */
    public static final String TENANT_ID = "tenant_id";

    /** 테넌트 문자열 코드. AI 서버가 ES 인덱스({@code items-nexon})를 고를 때 쓴다. */
    public static final String TENANT_CODE = "tenant_code";

    /** USER / ADMIN. GM 검토 큐 접근 판정에 쓴다. */
    public static final String ROLE = "role";

    private Claims() {}
}
