"""스트리밍 진입점 — **로컬에서는 안 보이는 것들**을 고정한다.

이 라운드의 결함 셋은 전부 개발 루프가 원리적으로 못 보는 것들이다
(`docs/05-Troubleshooting/로컬-프로세스로는-볼-수-없는-결함.md` 와 같은 계열):

  1. `X-Accel-Buffering: no` 누락 -> **로컬 dev 서버에는 프록시 버퍼가 없어서
     멀쩡히 흐른다.** 배포에서만 "전부 끝난 뒤 한꺼번에" 가 된다
  2. 스트리밍 경로에 한도가 안 걸림 -> 로컬에서는 한도에 안 걸리니 티가 안 난다.
     그런데 그건 하루 50회 상한을 **우회하는 경로**가 생겼다는 뜻이다
  3. `ensure_ascii=True` -> 한글이 `\\uXXXX` 로 나간다. 파싱은 되므로 서버
     테스트는 통과하고 **화면에서만** 깨진다

전부 네트워크 없이 확인한다.
"""

from __future__ import annotations

import asyncio
import json

from app.core.progress import (
    STAGE_BRANCH,
    STAGE_CACHE,
    STAGE_ROUTING,
    STAGE_THINKING,
    STAGE_TOOL,
    noop,
    safe_emit,
)
from app.main import app
from app.routers.assistant import _sse


# **직접 구현하지 않는다.** `app.routes` 를 한 겹만 보면 `include_router` 로
# 붙인 라우터가 안 보이고, 그러면 아래 검사들이 "라우트를 못 찾아서" 가 아니라
# 조용히 통과할 수도 있다. 이미 그 함정을 푼 쪽을 재사용한다.
from tests.test_route_auth_coverage import _walk  # noqa: E402


def _route(path: str):
    for route in _walk(app.routes):
        if getattr(route, "path", None) == path:
            return route
    raise AssertionError(f"라우트가 없다: {path}")


class TestSseFraming:
    def test_한_건은_data_한_줄과_빈_줄이다(self):
        out = _sse({"type": "progress", "stage": "cache"})
        assert out.startswith("data: ")
        assert out.endswith("\n\n"), "빈 줄이 없으면 이벤트 경계가 안 생긴다"
        assert out.count("\n\n") == 1

    def test_한글이_이스케이프되지_않는다(self):
        # `ensure_ascii=True` 면 파싱은 되지만 화면에서만 깨진다 — 서버 쪽
        # 검사로는 절대 안 잡히는 종류다.
        out = _sse({"type": "done", "result": {"answer": "검을 찾았습니다"}})
        assert "검을 찾았습니다" in out
        assert "\\u" not in out

    def test_본문이_다시_파싱된다(self):
        payload = {"type": "progress", "stage": STAGE_TOOL, "tool": "search_items"}
        body = _sse(payload)[len("data: "):].strip()
        assert json.loads(body) == payload

    def test_줄바꿈이_들어가도_이벤트가_안_깨진다(self):
        # 답변에 개행이 들어가는 건 정상이다. JSON 이 `\n` 을 이스케이프하므로
        # `data:` 는 한 줄로 남아야 한다 — 안 그러면 SSE 프레이밍이 깨진다.
        out = _sse({"type": "done", "result": {"answer": "첫 줄\n둘째 줄"}})
        assert out.count("\n") == 2, "본문 개행이 그대로 나가면 이벤트가 쪼개진다"


class TestDeploymentOnlyDefects:
    """배포에서만 드러나는 것들을 코드에서 미리 잡는다."""

    def test_스트림_라우트가_존재한다(self):
        # 아래 검사들이 "라우트가 없어서" 통과하는 걸 막는다.
        assert "POST" in _route("/api/assistant/stream").methods

    def test_버퍼링_해제_헤더가_붙는다(self):
        # nginx 는 프록시 응답을 기본 버퍼링한다. 이 헤더가 없으면 **로컬은
        # 멀쩡하고 배포만** 안 흐른다.
        import inspect

        from app.routers import assistant

        src = inspect.getsource(assistant.assistant_stream)
        assert "X-Accel-Buffering" in src
        assert '"no"' in src

    def test_스트림도_같은_한도를_탄다(self):
        # 안 걸면 하루 50회 상한을 **우회하는 경로**가 생긴다. 로컬에서는
        # 한도에 안 닿으니 티가 안 난다.
        from app.core.rate_limit import limit_assistant

        for path in ("/api/assistant", "/api/assistant/stream"):
            calls = [d.call for d in _route(path).dependant.dependencies]
            assert limit_assistant in calls, f"{path} 에 한도가 안 걸렸다"


class TestProgressCallback:
    def test_콜백이_터져도_파이프라인은_계속한다(self):
        # 클라이언트가 끊었다고 요청이 실패하면 안 된다.
        async def boom(stage, detail):
            raise RuntimeError("클라이언트가 끊김")

        asyncio.run(safe_emit(boom, STAGE_CACHE, hit=False))  # 예외가 안 나면 통과

    def test_기본값은_아무것도_안_한다(self):
        assert asyncio.run(noop(STAGE_CACHE, {"hit": True})) is None

    def test_단계_이름은_서로_다르다(self):
        stages = [STAGE_CACHE, STAGE_ROUTING, STAGE_BRANCH, STAGE_THINKING, STAGE_TOOL]
        assert len(set(stages)) == len(stages)

    def test_detail_이_그대로_전달된다(self):
        seen = []

        async def record(stage, detail):
            seen.append((stage, detail))

        asyncio.run(safe_emit(record, STAGE_TOOL, tool="search_items", step=2, failed=False))
        assert seen == [(STAGE_TOOL, {"tool": "search_items", "step": 2, "failed": False})]


class TestDailyBudgetRuleDoesNotFork:
    """두 라우트가 **같은 정산 함수**를 쓰는지 (ADR-0044).

    일일 예산은 이제 응답을 보고 소비한다(캐시 적중이면 안 함). 그 규칙이 두
    라우트에 각자 적혀 있으면 한쪽만 고쳐지고, **스트리밍 경로가 곧 상한
    우회로**가 된다. 라우트를 늘릴 때 조용히 빠뜨리기 쉬운 종류라 소스로 박는다.
    """

    def test_핸들러가_ask_를_직접_부르지_않는다(self):
        import inspect

        from app.routers import assistant

        for handler in (assistant.assistant, assistant.assistant_stream):
            src = inspect.getsource(handler)
            assert "_ask_metered" in src, f"{handler.__name__} 이 정산을 안 거친다"
            assert "await ask(" not in src, (
                f"{handler.__name__} 이 ask() 를 직접 불러 정산을 건너뛴다"
            )

    def test_정산_함수가_적중을_실제로_가른다(self):
        # 위 검사는 "부르기만 하면" 통과한다. 실제로 갈라주는지는 따로 본다.
        import inspect

        from app.routers.assistant import _ask_metered

        src = inspect.getsource(_ask_metered)
        assert "consume_daily" in src
        assert '"hit"' in src, "캐시 적중 여부를 안 본다"


class TestEveryExitConsumesTheBudget:
    """나가는 길이 셋이고 **셋 다** 예산을 소비하는가.

    `except Exception` 으로 실패만 잡던 판본은 **취소를 놓쳤다** —
    `asyncio.CancelledError` 는 `BaseException` 이라 그 그물을 통과한다. 그리고
    스트리밍 경로는 클라이언트가 끊길 때마다 `task.cancel()` 을 부른다. 즉
    "탭을 닫으면 LLM 값은 나가고 하루 예산은 안 깎인다".

    **소스 스캔으로는 못 잡는다.** 위의 `TestDailyBudgetRuleDoesNotFork` 는
    `consume_daily` 라는 글자가 있는지만 보는데, 그 글자는 예전 판본에도 있었다.
    그래서 여기서는 실제로 태스크를 취소해 **동작**을 본다.
    """

    @staticmethod
    def _patch(monkeypatch, ask_impl):
        from app.core.auth import Actor
        from app.routers import assistant

        consumed: list[str] = []

        async def fake_consume(actor):
            consumed.append(actor.tenant_code)

        monkeypatch.setattr(assistant, "ask", ask_impl)
        monkeypatch.setattr(assistant, "consume_daily", fake_consume)
        actor = Actor(user_id=3, tenant_id=1, tenant_code="nexon", role="USER")
        request = assistant.AssistantRequest(query="검 찾아줘")
        return consumed, actor, request

    def test_취소돼도_소비한다(self, monkeypatch):
        async def hangs(**kwargs):
            await asyncio.sleep(3600)  # 응답을 기다리는 중에 클라이언트가 끊는다

        consumed, actor, request = self._patch(monkeypatch, hangs)

        async def scenario():
            from app.routers.assistant import _ask_metered

            task = asyncio.create_task(_ask_metered(actor, None, None, request))
            await asyncio.sleep(0)  # 태스크가 await 지점까지 가게 한다
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        asyncio.run(scenario())
        assert consumed == ["nexon"], "취소가 예산을 공짜로 만든다 — 상한 우회로다"

    def test_실패해도_소비한다(self, monkeypatch):
        async def boom(**kwargs):
            raise RuntimeError("업스트림 장애")

        consumed, actor, request = self._patch(monkeypatch, boom)

        async def scenario():
            from app.routers.assistant import _ask_metered

            try:
                await _ask_metered(actor, None, None, request)
            except RuntimeError:
                pass

        asyncio.run(scenario())
        assert consumed == ["nexon"]

    def test_적중이면_소비하지_않는다(self, monkeypatch):
        # **반대 방향도 같이 본다.** 이게 없으면 "항상 소비한다"로 고쳐도
        # 위 둘이 통과한다 — ADR-0044 가 막은 쪽이 도로 열린다.
        async def cached(**kwargs):
            return {"answer": "…", "cache": {"hit": True}}

        consumed, actor, request = self._patch(monkeypatch, cached)
        asyncio.run(_ask_metered_once(actor, request))
        assert consumed == []

    def test_미적중이면_소비한다(self, monkeypatch):
        async def fresh(**kwargs):
            return {"answer": "…", "cache": {"hit": False}}

        consumed, actor, request = self._patch(monkeypatch, fresh)
        asyncio.run(_ask_metered_once(actor, request))
        assert consumed == ["nexon"]


async def _ask_metered_once(actor, request):
    from app.routers.assistant import _ask_metered

    return await _ask_metered(actor, None, None, request)
