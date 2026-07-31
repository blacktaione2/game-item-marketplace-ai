"""JWT 검증 테스트 (ADR-0023).

**검증기가 두 벌이라서** 이 테스트가 필요하다. 발급은 Spring Boot가 하고 검증은
여기서도 한다 — 게이트웨이를 두지 않기로 한 대가다. 두 구현이 갈라지면 발급은
되는데 이쪽만 거부하는 상태가 되고, 그 증상은 "왜 AI 서버만 401이지"로 나타나
원인을 찾기 어렵다.

여기서 재는 것은 **거부해야 할 것을 거부하는가**다. 통과 케이스 하나보다
실패 케이스들이 중요하다 — 서명 검증이 꺼져 있어도 통과 테스트는 초록이다.
"""

from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi import HTTPException

from app.core.auth import (
    CLAIM_ROLE,
    CLAIM_TENANT_CODE,
    CLAIM_TENANT_ID,
    Actor,
    require_admin,
    verify_token,
)
from app.core.config import get_settings

SECRET = get_settings().jwt_secret
ISSUER = get_settings().jwt_issuer


def make_token(secret=SECRET, issuer=ISSUER, expires_in=3600, drop=None, **overrides):
    """백엔드가 발급하는 것과 같은 모양의 토큰."""
    now = datetime.now(tz=timezone.utc)
    payload = {
        "sub": "3",
        "iss": issuer,
        "iat": now,
        "exp": now + timedelta(seconds=expires_in),
        CLAIM_TENANT_ID: 1,
        CLAIM_TENANT_CODE: "nexon",
        CLAIM_ROLE: "USER",
    }
    payload.update(overrides)
    if drop:
        payload.pop(drop)
    return jwt.encode(payload, secret, algorithm="HS256")


def test_valid_token_yields_actor():
    actor = verify_token(make_token())
    assert actor == Actor(user_id=3, tenant_id=1, tenant_code="nexon", role="USER")


def test_expired_token_is_rejected():
    """만료가 실제로 검사되는지. exp를 안 보면 토큰이 영구 유효해진다."""
    with pytest.raises(HTTPException) as caught:
        verify_token(make_token(expires_in=-1))
    assert caught.value.status_code == 401


def test_wrong_signature_is_rejected():
    """**이 테스트가 없으면 서명 검증이 꺼져 있어도 모른다.**"""
    with pytest.raises(HTTPException) as caught:
        verify_token(make_token(secret="another_secret_that_is_long_enough_32"))
    assert caught.value.status_code == 401


def test_wrong_issuer_is_rejected():
    with pytest.raises(HTTPException):
        verify_token(make_token(issuer="somebody-else"))


@pytest.mark.parametrize("claim", ["sub", "exp", "iss", CLAIM_TENANT_ID, CLAIM_TENANT_CODE, CLAIM_ROLE])
def test_missing_required_claim_is_rejected(claim):
    """클레임이 빠지면 KeyError로 500이 나는 게 아니라 401이어야 한다."""
    with pytest.raises(HTTPException) as caught:
        verify_token(make_token(drop=claim))
    assert caught.value.status_code == 401


def test_garbage_is_rejected():
    with pytest.raises(HTTPException):
        verify_token("not-a-jwt")


def test_alg_none_is_rejected():
    """서명 없는 토큰을 받아주면 인증이 통째로 무의미해진다.

    `algorithms=["HS256"]`을 명시했으므로 막혀야 한다. 고전적인 JWT 취약점이라
    회귀로 박아둔다.
    """
    now = datetime.now(tz=timezone.utc)
    unsigned = jwt.encode(
        {
            "sub": "3",
            "iss": ISSUER,
            "exp": now + timedelta(hours=1),
            CLAIM_TENANT_ID: 1,
            CLAIM_TENANT_CODE: "nexon",
            CLAIM_ROLE: "ADMIN",
        },
        key="",
        algorithm="none",
    )
    with pytest.raises(HTTPException):
        verify_token(unsigned)


def test_tenant_comes_from_token_not_the_caller():
    """다른 테넌트를 주장해도 토큰에 적힌 값이 쓰인다.

    헤더 시절에는 이게 불가능했다 — 보내는 쪽이 곧 진실이었다.
    """
    actor = verify_token(make_token(**{CLAIM_TENANT_CODE: "ncsoft", CLAIM_TENANT_ID: 2}))
    assert actor.tenant_code == "ncsoft"
    assert actor.tenant_id == 2


# --- 역할 인가 -------------------------------------------------------------


def test_require_admin_blocks_normal_user():
    user = Actor(user_id=3, tenant_id=1, tenant_code="nexon", role="USER")
    with pytest.raises(HTTPException) as caught:
        require_admin(user)
    # 인증은 됐고 권한이 모자란 것이므로 403이다. 401이면 "다시 로그인하라"는
    # 잘못된 안내가 된다.
    assert caught.value.status_code == 403


def test_require_admin_allows_gm():
    admin = Actor(user_id=1, tenant_id=1, tenant_code="nexon", role="ADMIN")
    assert require_admin(admin) is admin
