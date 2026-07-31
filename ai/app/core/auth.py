"""JWT 검증 (ADR-0023).

## 왜 AI 서버가 따로 검증하는가

게이트웨이를 두지 않기로 했다. 4 OCPU 공유가 이유의 절반이고, 나머지 절반은
**이 서버가 어차피 직접 노출돼 있다**는 것이다 — 8000 포트가 열려 있고
비용(LLM 호출)이 나가는 쪽이 여기다. 게이트웨이를 세워도 그 앞을 우회하는
경로가 남으면 아무 의미가 없다.

대가로 **검증기가 두 벌**이 된다(여기와 `backend/.../security/`). 갈라지지
않게 클레임 이름을 아래 한 곳에 모으고, **발급은 백엔드만** 한다. 이 모듈은
검증 전용이다.

## 비밀키

대칭키(HS256)라 백엔드의 `jwt.secret`과 **같은 값**이어야 한다.
`OPENAI_API_KEY`가 `ai/.env`에만 있는 것과 달리 이건 두 곳에 있다 — 다르면
발급은 성공하는데 이 서버만 401을 내므로 증상이 헷갈린다.
"""

from __future__ import annotations

from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException, Request

from app.core.config import get_settings

# 백엔드 `security/Claims.java`와 짝이다. 한쪽을 고치면 다른 쪽도 고쳐야 한다.
CLAIM_TENANT_ID = "tenant_id"
CLAIM_TENANT_CODE = "tenant_code"
CLAIM_ROLE = "role"

_REQUIRED_CLAIMS = ["sub", "exp", "iss", CLAIM_TENANT_ID, CLAIM_TENANT_CODE, CLAIM_ROLE]

ROLE_ADMIN = "ADMIN"


@dataclass(frozen=True)
class Actor:
    """검증된 토큰에서 뽑아낸 행위자."""

    user_id: int
    tenant_id: int
    tenant_code: str
    role: str

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN


def verify_token(token: str) -> Actor:
    """서명·만료·발급자·필수 클레임을 검증한다.

    실패는 전부 같은 예외로 묶는다 — 어느 검증에서 걸렸는지 응답으로 알려주면
    토큰을 깎아 맞추는 데 쓸 수 있다.
    """
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=["HS256"],
            issuer=settings.jwt_issuer,
            options={"require": _REQUIRED_CLAIMS},
        )
    except jwt.InvalidTokenError as e:  # 만료·서명불일치·클레임누락이 전부 이 하위다
        raise HTTPException(status_code=401, detail="토큰이 유효하지 않습니다") from e

    return Actor(
        user_id=int(payload["sub"]),
        tenant_id=int(payload[CLAIM_TENANT_ID]),
        tenant_code=str(payload[CLAIM_TENANT_CODE]),
        role=str(payload[CLAIM_ROLE]),
    )


def require_actor(request: Request) -> Actor:
    """`Authorization: Bearer <token>` 를 요구한다.

    `fastapi.security.HTTPBearer`를 쓰지 않는 이유는 그쪽이 헤더 없음을 **403**
    으로 처리하기 때문이다. 인증이 없는 것(401)과 권한이 모자란 것(403)은 다른
    답이어야 한다 — ADR-0022에서 400과 501을 가른 것과 같은 이유다.
    """
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=401,
            detail="인증이 필요합니다 (Authorization: Bearer <token>)",
        )
    return verify_token(token)


def require_admin(actor: Actor = Depends(require_actor)) -> Actor:
    """GM 전용. 이상거래 검토 큐가 유일한 대상이다."""
    if not actor.is_admin:
        raise HTTPException(status_code=403, detail="GM 권한이 필요합니다")
    return actor
