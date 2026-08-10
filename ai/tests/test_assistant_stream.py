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
from app.services.forecast.exceptions import ItemNotFoundError
from app.services.search.exceptions import TenantIndexNotFoundError
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


class TestShowableExceptionsAreOneList:
    """두 라우트가 **하나의 표**에서 예외 목록을 얻는지 (ADR-0050).

    ADR-0049 는 `_SHOWABLE` 을 만들며 *"두 라우트가 같은 목록을 쓴다"* 고 적었지만
    **실제로는 목록이 둘이었다** — 튜플은 스트리밍만 쓰고, 비스트리밍은 `except`
    절 셋에 같은 여섯 개를 다시 열거했다. 오늘 두 목록이 일치하는 것은 같은
    사람이 같은 날 적었기 때문이지 구조 때문이 아니다.

    한쪽만 늘어나면 같은 예외가 한쪽에서는 404·422 안내가 되고 다른 쪽에서는
    "요청 처리에 실패했습니다"가 된다 — **어긋나도 아무 신호가 없는 종류**라
    소스로 박는다.
    """

    def _except_names(self, handler) -> list[str]:
        """핸들러의 `except` 절이 이름으로 잡는 것들."""
        import ast
        import inspect
        import textwrap

        tree = ast.parse(textwrap.dedent(inspect.getsource(handler)))
        names: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler) or node.type is None:
                continue
            targets = (
                node.type.elts if isinstance(node.type, ast.Tuple) else [node.type]
            )
            names += [t.id for t in targets if isinstance(t, ast.Name)]
        return names

    def test_비스트리밍이_예외를_다시_열거하지_않는다(self):
        from app.routers import assistant

        names = self._except_names(assistant.assistant)
        assert names, "표본이 틀렸습니다 — except 절을 하나도 못 찾았습니다"
        extra = [n for n in names if n not in ("_SHOWABLE", "Exception")]
        assert not extra, (
            f"비스트리밍 경로가 예외를 따로 열거합니다: {extra} — "
            "`_SHOWABLE_STATUS` 한 곳에서 나오게 하세요."
        )

    def test_스트리밍도_같은_이름을_쓴다(self):
        from app.routers import assistant

        assert "_SHOWABLE" in self._except_names(assistant.assistant_stream)

    def test_열거를_되살리면_걸린다(self):
        """**공허 방지 — 같은 식을 실패 방향으로.** 옛 모양을 그대로 만들어 본다."""

        async def old_shape():  # pragma: no cover - 표본
            try:
                return None
            except (TenantIndexNotFoundError, ItemNotFoundError):
                raise
            except Exception:
                raise

        names = self._except_names(old_shape)
        assert [n for n in names if n not in ("_SHOWABLE", "Exception")] == [
            "TenantIndexNotFoundError",
            "ItemNotFoundError",
        ]

    def _router_mappings(self) -> dict[str, int]:
        """개별 라우터가 매핑하는 **도메인 예외 → 상태 코드**. 소스에서 유도한다.

        **목록을 적지 않는다.** 첫 판본은 ADR-0049 가 적은 여섯 개를 그대로
        옮겼는데 개별 라우터가 매핑하는 것은 아홉이었다 — `TradeNotFoundError`
        가 빠져서 같은 조건에 `/api/anomaly/detect` 는 404, `/api/assistant` 는
        **500** 이었다(배포에서 실측). *목록을 하나로 합치면서, 무엇이 그 목록에
        들어가야 하는지를 다시 열거로 정한 것*이다.

        **범위는 `app/routers/*.py` 에서 `assistant` 를 뺀 전부다** — 세 라우터라고
        적어두면 그 문장이 곧 다음 열거가 된다. 오늘 4xx 를 내는 것이 anomaly·
        forecast·search 셋뿐이라 결과가 같을 뿐이다.

        **내장 예외는 뺀다. 다만 조용히 건너뛰지 않고 아래에서 단언한다** —
        `test_no_builtin_catch_carries_a_4xx` 가 *"내장 예외를 4xx 로 옮기는 catch
        자체가 없다"* 를 고정한다. 건너뛰기만 하면 이 함수가 결함을 가려준다.

        > 첫 판본은 그 근거를 *"내장 예외는 요청 파싱에서 나온다"* 라고 적었는데
        > **틀렸다** (ADR-0050 정정). `forecast.py` 의 `except ValueError -> 400` 은
        > `forecast_price` **파이프라인 안**의 검증을 잡고 있었다. 확인하지 않고
        > 쓴 근거였고, 그 근거가 있었기 때문에 그 catch 를 결함으로 못 봤다.
        """
        import ast
        import builtins
        import pathlib

        import app.routers as routers_pkg

        root = pathlib.Path(routers_pkg.__file__).parent
        found: dict[str, int] = {}
        for path in sorted(root.glob("*.py")):
            if path.stem in ("assistant", "__init__"):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for handler in ast.walk(tree):
                if not isinstance(handler, ast.ExceptHandler) or handler.type is None:
                    continue
                targets = (
                    handler.type.elts
                    if isinstance(handler.type, ast.Tuple)
                    else [handler.type]
                )
                names = [t.id for t in targets if isinstance(t, ast.Name)]
                status = None
                for node in ast.walk(handler):
                    if isinstance(node, ast.keyword) and node.arg == "status_code":
                        status = getattr(node.value, "value", None)
                if status is None or status == 500:
                    continue
                for name in names:
                    if hasattr(builtins, name):
                        continue
                    found[name] = status
        return found

    def test_개별_라우터가_매핑하는_것을_전부_물려받는다(self):
        from app.routers.assistant import _SHOWABLE_STATUS

        mine = {cls.__name__: status for cls, status in _SHOWABLE_STATUS.items()}
        theirs = self._router_mappings()
        assert len(theirs) >= 6, f"표본이 이상합니다 — 라우터에서 찾은 매핑 {theirs}"

        missing = {n: s for n, s in theirs.items() if n not in mine}
        assert not missing, (
            f"개별 라우터는 매핑하는데 통합 진입점은 500 을 내는 예외가 있습니다: "
            f"{missing} — `_SHOWABLE_STATUS` 에 넣으세요."
        )
        disagreeing = {
            n: (mine[n], s) for n, s in theirs.items() if mine[n] != s
        }
        assert not disagreeing, (
            f"같은 예외에 다른 상태 코드를 냅니다 (통합, 개별): {disagreeing}"
        )

    def test_내장_예외를_4xx_로_옮기는_catch_가_없다(self):
        """**제외를 단언으로 바꾼다** (ADR-0050 정정).

        위 유도는 내장 예외를 건너뛴다. 건너뛰기만 하면 `except ValueError -> 400`
        같은 자리가 **검사에 안 보인 채로** 남는다 — 실제로 그랬다.

        그런 catch 는 그 자체로 결함 신호다. `detail=str(e)` 로 내보내는데 범주
        기반이라, 파이프라인 안의 ES·numpy·torch 가 내는 예외까지 내부 메시지를
        4xx 본문에 싣는다. `test_error_detail_leak.py` 가 `detail=str(e)` 를
        허용하는 근거(*"도메인 예외라 우리가 쓴 메시지"*)가 거기서만 거짓이었다.

        `Exception -> 500` 은 예외다 — 일반 문장을 쓰고 예외를 안 싣는다.
        """
        import ast
        import builtins
        import pathlib

        import app.routers as routers_pkg

        offenders = []
        for path in sorted(pathlib.Path(routers_pkg.__file__).parent.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for handler in ast.walk(tree):
                if not isinstance(handler, ast.ExceptHandler) or handler.type is None:
                    continue
                targets = (
                    handler.type.elts
                    if isinstance(handler.type, ast.Tuple)
                    else [handler.type]
                )
                names = [t.id for t in targets if isinstance(t, ast.Name)]
                status = None
                for node in ast.walk(handler):
                    if isinstance(node, ast.keyword) and node.arg == "status_code":
                        status = getattr(node.value, "value", None)
                if status is None or status >= 500:
                    continue
                offenders += [
                    f"{path.name}: except {n} -> {status}"
                    for n in names
                    if hasattr(builtins, n)
                ]
        assert not offenders, (
            "내장 예외를 4xx 로 옮기는 catch 가 있습니다: "
            + ", ".join(offenders)
            + " — 도메인 예외를 만들어 잡으세요. 범주 기반 catch 는 "
            "내부 예외 메시지를 응답 본문에 싣습니다."
        )

    def test_그_단언이_실제로_잡는다(self):
        """**공허 방지.** 고치기 전 모양(`except ValueError -> 400`)을 만들어 본다."""
        import ast
        import builtins

        tree = ast.parse(
            "try:\n"
            "    pass\n"
            "except ValueError as e:\n"
            "    raise HTTPException(status_code=400, detail=str(e)) from e\n"
        )
        hits = []
        for handler in ast.walk(tree):
            if not isinstance(handler, ast.ExceptHandler) or handler.type is None:
                continue
            names = [handler.type.id] if isinstance(handler.type, ast.Name) else []
            status = None
            for node in ast.walk(handler):
                if isinstance(node, ast.keyword) and node.arg == "status_code":
                    status = getattr(node.value, "value", None)
            if status is not None and status < 500:
                hits += [n for n in names if hasattr(builtins, n)]
        assert hits == ["ValueError"]

    def test_그_유도가_실제로_라우터를_읽는다(self):
        """**공허 방지.** 0개를 읽으면 위 검사는 공짜로 통과한다."""
        theirs = self._router_mappings()
        # 실제로 라우터에 있는 매핑 하나로 확인한다 — 지어낸 이름으로 하면
        # "읽었는가" 가 아니라 정규식만 시험하게 된다.
        assert theirs.get("TradeNotFoundError") == 404, theirs
        assert "ValueError" not in theirs, "내장 예외를 걸러야 한다"

    def test_표에_있는_예외는_전부_상태코드가_나온다(self):
        from app.routers.assistant import _SHOWABLE, _SHOWABLE_STATUS, _showable_status

        assert set(_SHOWABLE) == set(_SHOWABLE_STATUS), (
            "`except` 가 쓰는 튜플이 표에서 유도되지 않습니다"
        )
        # 생성자 시그니처가 제각각이라 `__new__` 로 만든다 — isinstance 만 보면 된다.
        for cls in _SHOWABLE_STATUS:
            assert _showable_status(cls.__new__(cls)) == _SHOWABLE_STATUS[cls]


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
