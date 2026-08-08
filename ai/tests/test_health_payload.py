"""`/health` 가 **실제로 도는 설정**을 내되 비밀은 안 내는지 (ADR-0045).

이 엔드포인트는 인증 없이 열려 있다(사설망 전제). 그래서 두 방향을 같이 고정한다 —
**실을 것은 실려야** 하고(안 그러면 배포에서 확인할 방법이 없다) **비밀은 절대
안 실려야** 한다.

`llm_model` 이 여기 있는 이유는 배포에서 두 번 물린 함정 때문이다: `ai/.env` 가
코드 기본값을 **덮는다.** 모델을 바꾸고 재빌드해도 `.env` 에 옛 값이 남아 있으면
**옛 모델이 계속 돈다.** `grep` 으로 확인하라고 안내하면 사람이 읽고 판단해야
하고, 실제로 그 단계에서 놓쳤다.
"""

from __future__ import annotations

import asyncio
import json

from app.core.config import Settings
from app.routers.health import health


def _call(**overrides) -> dict:
    settings = Settings(**overrides)
    return asyncio.run(health(settings))


class TestReportsWhatActuallyRuns:
    def test_실제_모델명을_낸다(self):
        body = _call(openai_model="gpt-5.4-mini-2026-03-17")
        assert body["llm_model"] == "gpt-5.4-mini-2026-03-17"

    def test_설정이_바뀌면_따라간다(self):
        # 상수를 박아두면 이 검사는 통과하면서 아무것도 안 잡는다.
        assert _call(openai_model="model-a")["llm_model"] == "model-a"
        assert _call(openai_model="model-b")["llm_model"] == "model-b"

    def test_폴백은_키가_있을_때만_모델을_밝힌다(self):
        with_key = _call(anthropic_api_key="k", anthropic_model="claude-x")
        assert with_key["llm_fallback"] is True
        assert with_key["llm_fallback_model"] == "claude-x"

        without = _call(anthropic_api_key="", anthropic_model="claude-x")
        assert without["llm_fallback"] is False
        # **안 붙었는데 모델명이 보이면 "설정됐다" 로 오독된다.**
        assert without["llm_fallback_model"] is None


class TestNeverLeaksSecrets:
    """`/health` 는 인증이 없다. 여기 실리는 것은 비밀이 아닌 사실뿐이어야 한다."""

    def test_키와_시크릿이_본문에_없다(self):
        secrets = {
            "openai_api_key": "sk-openai-SECRET-VALUE",
            "anthropic_api_key": "sk-anthropic-SECRET-VALUE",
            "jwt_secret": "jwt-SECRET-VALUE-0123456789abcdef0123456789abcdef",
        }
        body = json.dumps(_call(**secrets), ensure_ascii=False)
        leaked = [name for name, value in secrets.items() if value in body]
        assert leaked == [], f"본문에 비밀이 실렸다: {leaked}"

    def test_이_검사가_공허하지_않다(self):
        # 위 검사가 "본문이 비어서" 통과하는 걸 막는다 — 실제로 값을 싣고 있고,
        # 그중 하나는 비밀과 같은 자리(설정)에서 온다.
        body = _call(openai_model="model-a")
        assert body["llm_model"] == "model-a"
        assert body["status"] == "ok"
