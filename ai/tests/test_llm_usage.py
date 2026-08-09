"""요청 단위 LLM 호출 카운터.

복합 분기의 `llm_calls` 가 상수(`len(tool_calls) + 1`)였고 **두 군데서 틀렸다** —
병렬 도구 호출과 도구 내부 호출. 실측으로 확인하고 세는 쪽으로 바꿨다
(`app/core/llm_usage.py`).

여기서 고정하는 것은 **세는 장치가 실제로 세는가**이고, 그중 하나는 네트워크
없이 확인하기 어려워 보이지만 아니다 — 어려운 부분이 `asyncio` 의 컨텍스트
복사 규칙이라 순수 단위로 재현된다.
"""

from __future__ import annotations

import asyncio

from app.core.llm_usage import count_llm_calls, note_call
from app.core.metrics import record_llm_call


class TestCounting:
    def test_스코프_밖에서는_아무_일도_안_한다(self):
        # 스크립트·MCP stdio 처럼 스코프 없이 부르는 경로가 있다. 터지면 안 된다.
        note_call()

    def test_스코프_안의_호출을_센다(self):
        with count_llm_calls() as usage:
            note_call()
            note_call()
        assert usage.calls == 2

    def test_스코프를_벗어나면_안_센다(self):
        with count_llm_calls() as usage:
            note_call()
        note_call()
        assert usage.calls == 1

    def test_계측_지점은_하나다(self):
        """클라이언트는 `record_llm_call` 만 부른다 — 거기서 같이 올라가야 한다.

        따로 부르게 하면 프로바이더가 하나 늘 때 조용히 빠진다.
        """
        with count_llm_calls() as usage:
            record_llm_call("openai", ok=True)
            record_llm_call("openai", ok=False)
        assert usage.calls == 2, "실패도 센다 — 요금은 나갔을 수 있다"


class TestItSurvivesGather:
    """**여기가 진짜 함정이다.**

    `asyncio.gather` 는 코루틴을 태스크로 감싸고 태스크는 컨텍스트를 **복사**한다.
    그래서 `ContextVar.set()` 로 숫자를 갱신하면 자식의 증가가 부모에 안 보인다.
    검색 파이프라인이 질의이해와 도메인 판정을 바로 `gather` 로 던지므로, 이걸
    틀리면 **가장 흔한 경로에서 0 을 센다** — 그리고 증상은 "예전과 같은 숫자"라
    눈에 안 띈다.

    가변 객체를 담으면 복사된 컨텍스트도 같은 객체를 가리켜 증가가 보인다.
    """

    def test_gather_로_나눠_부른_것도_합쳐진다(self):
        async def leaf():
            note_call()

        async def scenario():
            with count_llm_calls() as usage:
                await asyncio.gather(leaf(), leaf(), leaf())
            return usage.calls

        assert asyncio.run(scenario()) == 3

    def test_create_task_도_합쳐진다(self):
        async def leaf():
            note_call()

        async def scenario():
            with count_llm_calls() as usage:
                task = asyncio.create_task(leaf())
                await task
            return usage.calls

        assert asyncio.run(scenario()) == 1

    def test_중첩된_깊이에서도_센다(self):
        """에이전트 → MCP 도구 → 검색 파이프라인 → LLM 의 모양이다."""

        async def deep():
            await asyncio.gather(*(_inner() for _ in range(2)))

        async def _inner():
            note_call()

        async def scenario():
            with count_llm_calls() as usage:
                await asyncio.gather(deep(), deep())
            return usage.calls

        assert asyncio.run(scenario()) == 4


class TestAgentBranchUsesTheMeasurement:
    def test_유도_공식이_남아있지_않다(self):
        """`len(tool_calls) + 1` 로 되돌아가면 같은 결함이 재발한다.

        실측(gpt-5.4-mini, 복합 3건 × 2회)에서 그 공식은 **전부 과소**였다:
        보고 4/실제 8, 3/5, 5/7. 마지막 건은 한 스텝이 도구를 2개 부른 경우다.
        """
        import inspect

        from app.services.assistant import pipeline

        src = inspect.getsource(pipeline._agent_branch)
        assert "count_llm_calls" in src
        assert "usage.calls" in src
        assert 'len(result["tool_calls"]) + 1' not in src
