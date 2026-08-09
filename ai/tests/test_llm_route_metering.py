"""LLM을 부르는 경로에는 한도가 걸려 있는가.

## 왜 이 검사가 있는가

`POST /api/search` 가 **인증만 있고 한도가 없는 채로 공개 배포에 있었다.**
`run_search` 는 `understand_query` 와 `judge_in_domain` 을 부르므로 **호출당
LLM 2회**인데, `limit_assistant`(20회/분) 도 `consume_daily`(50회/일) 도 없었다.
토큰 하나면 비용 방어 3계층이 통째로 우회됐다.

원인은 그 파일이 아니라 근거의 모양이다. `core/rate_limit.py` 는 막을 대상을
"`/api/assistant` 하나 — 조회 계열은 싸다"라고 적었는데, 그 일반화는
`/api/forecast`·`/api/anomaly/detect` 에 대해서는 참이고(둘 다 `llm_client` 를
주입받지 않는다) `/api/search` 에 대해서만 거짓이었다. **예외가 일반화 안에
숨었다.**

## 그래서 열거하지 않는다

ADR-0033 이 인증에서 배운 것과 같다 — 경로마다 개별로 붙이는 규칙은 빠뜨려도
아무 신호가 없다. 다만 이번에는 한 걸음 더 간다: **대상 목록도 적지 않는다.**
목록을 적으면 그 목록이 다음에 새는 것이 되고, 이 저장소는 이미 그걸
두 번 겪었다(`docker-compose.deploy.yml` 의 서비스 목록, 도메인 게이트
프롬프트의 아이템 종류 목록 — 사례 28).

대신 **라우트 시그니처에서 유도한다**: `get_llm_client` 를 주입받는 라우트는
LLM 비용을 쓴다는 뜻이므로, `limit_assistant` 도 가져야 한다. 새 LLM 경로가
생기면 이 파일을 안 고쳐도 걸린다.

## 소비까지는 시그니처로 못 본다

`limit_assistant` 는 **확인만** 하고 일일 카운터를 올리지 않는다 — 올리는 것은
응답 뒤의 `consume_daily` 다(ADR-0044). 검사만 있고 소비가 없으면 **일일 한도는
영원히 0을 읽는다.** 그건 의존성 그래프에 안 나타나므로 소스를 훑어서 본다.
"""

from __future__ import annotations

import ast
import inspect
import textwrap

import app.core.rate_limit as rate_limit_module
from app.core.rate_limit import consume_daily, limit_assistant
from app.main import app
from app.services.llm.dependencies import get_llm_client


def _walk(routes):
    """`_IncludedRouter` 안쪽까지 내려가 실제 라우트를 전부 낸다.

    `test_route_auth_coverage.py` 와 같은 이유다 — 한 겹만 보면 포함된 라우터가
    안 보이고, 그러면 이 검사는 **0개를 세어 공짜로 통과한다.**
    """
    for route in routes:
        included = getattr(route, "original_router", None)
        if included is not None:
            yield from _walk(included.routes)
        else:
            yield route


def _dependency_callables(route) -> set:
    """이 라우트가 의존하는 호출 가능 객체 (중첩 한 겹 포함)."""
    found = set()
    for dependency in route.dependant.dependencies:
        found.add(dependency.call)
        for nested in dependency.dependencies:
            found.add(nested.call)
    return found


def _called_names(func) -> set[str]:
    """이 함수가 **실제로 부르는** 이름들.

    **문자열 스캔이 아니라 AST 다.** 첫 판본은 `"consume_daily" in source` 였는데,
    그러면 `# TODO: consume_daily 를 붙이자` 라는 **주석 한 줄로 검사가 뚫린다** —
    실제로 배포에 나갔던 결함을 막으라고 만든 검사가 주석에 속는다. 문자열 리터럴도
    같은 문제다. AST 는 주석을 애초에 안 싣는다.
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            target = node.func
            name = getattr(target, "id", None) or getattr(target, "attr", None)
            if name:
                names.add(name)
    return names


def _consumes_daily(endpoint) -> bool:
    """이 핸들러가 일일 예산을 소비하는가 — **간접 호출 한 겹까지** 본다.

    직접 부르는 경우(`/api/search`)와 같은 모듈의 헬퍼를 거치는 경우
    (`/api/assistant` → `_ask_metered`)가 둘 다 있다. 본문만 읽으면 후자를
    결함으로 잘못 지목한다 — 첫 판본이 실제로 그렇게 헛발을 짚었다.

    **깊이는 한 겹이다.** 두 겹 이상으로 숨기면 이 검사는 못 본다. 지금 두 모양이
    전부라서 그 이상은 사지 않았고, 못 보는 범위를 적어두는 것으로 갈음한다.
    """
    called = _called_names(endpoint)
    if "consume_daily" in called:
        return True
    module = inspect.getmodule(endpoint)
    for name, helper in vars(module).items():
        # **`in source` 가 아니라 실제 호출 여부다.** 이름이 주석이나 문자열에만
        # 나와도 통과하던 자리다.
        if not inspect.isfunction(helper) or name not in called:
            continue
        if "consume_daily" in _called_names(helper):
            return True
    return False


def _llm_routes():
    """LLM 클라이언트를 주입받는 라우트 — 즉 **비용이 나가는 경로.**"""
    return [
        route
        for route in _walk(app.routes)
        if hasattr(route, "dependant")
        and get_llm_client in _dependency_callables(route)
    ]


class TestEveryLlmRouteIsMetered:
    def test_there_is_something_to_check(self):
        """LLM 라우트를 0개로 세면 아래 검사는 공짜로 통과한다."""
        paths = {route.path for route in _llm_routes()}
        assert paths, "LLM 라우트를 하나도 못 찾았습니다 — 주입 방식이 바뀌었습니까?"
        # 개수만 세면 엉뚱한 걸 세도 통과한다. 아는 경로가 잡히는지도 본다.
        assert "/api/assistant" in paths
        assert "/api/search" in paths

    def test_cheap_routes_are_not_swept_in(self):
        """**반대 방향 공허 방지.** 모든 라우트를 LLM 경로로 세고 있으면,
        위 검사는 통과하지만 아무것도 구별하지 못한 것이다.

        `/api/forecast` 와 `/api/anomaly/detect` 는 실제로 LLM 을 안 부른다 —
        `llm_client` 를 주입받지도 않는다. 이 둘이 목록에 들어오면 유도 기준이
        고장 난 것이다.
        """
        paths = {route.path for route in _llm_routes()}
        assert "/api/forecast" not in paths
        assert "/api/anomaly/detect" not in paths

    def test_llm_routes_depend_on_the_limiter(self):
        unmetered = [
            f"{sorted(route.methods)} {route.path}"
            for route in _llm_routes()
            if limit_assistant not in _dependency_callables(route)
        ]
        assert not unmetered, (
            "LLM 을 부르는데 한도가 없는 경로가 있습니다: "
            + ", ".join(unmetered)
            + " — `Depends(limit_assistant)` 를 붙이세요."
        )

    def test_llm_routes_also_consume_the_daily_budget(self):
        """확인과 소비는 서로 다른 함수다 — 하나만 있으면 한도가 조용히 사라진다.

        `limit_assistant` 는 일일 카운터를 **읽기만** 한다. `consume_daily` 를
        빠뜨리면 그 카운터는 영원히 0이고, 50회/일은 있으나 마나가 된다.
        의존성 그래프에는 안 나타나므로 소스를 본다.

        > **처음 판본은 핸들러 본문만 읽었고, 그래서 헛발을 짚었다.**
        > `/api/assistant` 는 `_ask_metered` 를 거쳐 소비하므로 핸들러 소스에
        > `consume_daily` 가 없다 — 올바른 코드를 결함으로 지목했다.
        > `_한_단계_따라간다` 가 그 간접 호출을 따라간다.
        >
        > **깊이는 한 단계다.** 두 단계 이상으로 숨기면 이 검사는 못 본다.
        > 지금 두 모양(직접 호출 / 헬퍼 한 겹)이 전부라서 그 이상은 사지 않았다.
        """
        missing = [
            f"{sorted(route.methods)} {route.path}"
            for route in _llm_routes()
            if not _consumes_daily(route.endpoint)
        ]
        assert not missing, (
            "일일 예산을 소비하지 않는 LLM 경로가 있습니다: "
            + ", ".join(missing)
            + " — 응답 뒤 `finally` 에서 `consume_daily(actor)` 를 부르세요."
        )

    def test_the_scan_can_actually_fail(self):
        """**위 검사가 항상 통과하는 것은 아닌지** 확인한다."""

        async def handler_without_consume():  # pragma: no cover - 표본
            return {"ok": True}

        assert not _consumes_daily(handler_without_consume)

    def test_a_comment_does_not_count_as_calling_it(self):
        """**첫 판본은 이 표본에 속았다.**

        `"consume_daily" in source` 였을 때 아래 핸들러는 **통과했다** — 주석
        한 줄이 검사를 뚫는다. 실제로 배포에 나갔던 결함을 막으라고 만든
        검사가 그러면 장식이다. 지금은 AST 로 *호출*만 본다.
        """

        async def handler_that_only_mentions_it():  # pragma: no cover - 표본
            # TODO: consume_daily(actor) 를 붙여야 한다
            note = "consume_daily"
            return {"note": note}

        assert "consume_daily" in inspect.getsource(handler_that_only_mentions_it), (
            "표본에 그 이름이 없으면 이 검사는 아무것도 구별하지 못한다"
        )
        assert not _consumes_daily(handler_that_only_mentions_it)

    def test_the_one_hop_resolution_actually_resolves(self):
        """헬퍼를 거치는 모양을 **실제로 통과시키는지.**

        위 검사들과 짝이다. 그쪽은 "못 잡는 게 아니다", 이건 "헛발을 짚지
        않는다" — 처음 판본이 틀린 쪽이 이 방향이었다.
        """
        indirect = [
            route
            for route in _llm_routes()
            if "consume_daily" not in _called_names(route.endpoint)
        ]
        assert indirect, (
            "간접 호출 표본이 사라졌습니다 — 이 검사가 공허해졌는지 확인하세요"
        )
        assert all(_consumes_daily(route.endpoint) for route in indirect)

    def test_the_one_hop_can_also_fail(self):
        """**간접 경로의 실패 방향도 본다.**

        위 검사는 *실제 라우터 모듈*의 헬퍼로 성공만 확인한다. 실패 표본이
        테스트 모듈에만 있으면 `_consumes_daily` 의 **후반부(모듈 스캔)가 실패
        방향으로 한 번도 실행되지 않는다** — 그 절반은 통과하는 모습만 본 코드가
        된다. 그래서 여기서 같은 모듈 안에 "헬퍼를 부르지만 그 헬퍼가 소비하지
        않는" 모양을 만든다.
        """

        async def handler_via_innocent_helper():  # pragma: no cover - 표본
            return _helper_that_does_not_consume()

        assert not _consumes_daily(handler_via_innocent_helper)

    def test_the_dependency_scan_can_actually_fail(self):
        """같은 이유로, 한도 없는 라우트를 만들면 걸리는지 본다.

        > **이 검사는 한 라운드 동안 사라져 있었다** (ADR-0049). 모듈 레벨 헬퍼
        > `_helper_that_does_not_consume` 를 클래스 한가운데에 끼워 넣는 바람에
        > 이 메서드가 그 함수의 `return` **뒤로 중첩**됐고, pytest 가 수집하지
        > 않았다. 파일의 테스트 수는 9 → 11 로 **늘어서** 아무도 눈치채지
        > 못했다 — *순증가가 손실을 가린다.*
        > `test_collected_tests.py` 가 이 계열을 저장소 전체에서 막는다.
        """

        class _FakeDependant:
            dependencies: list = []

        class _FakeRoute:
            path = "/api/fake"
            methods = {"POST"}
            dependant = _FakeDependant()

        assert limit_assistant not in _dependency_callables(_FakeRoute())


def _helper_that_does_not_consume():  # pragma: no cover - 표본
    """`test_the_one_hop_can_also_fail` 이 모듈 스캔 분기를 실패 방향으로
    지나가게 하는 표본. **클래스 바깥에 둔다** — 안에 끼워 넣으면 그 아래 메서드가
    이 함수 본문으로 빨려 들어간다(위 참고)."""
    return {"ok": True}


def test_consume_daily_and_limiter_share_one_key_builder():
    """검사와 소비가 **같은 키 함수**를 쓰는지.

    다른 키를 만들면 검사는 영원히 0을 읽고 증가는 아무도 안 보는 카운터를
    올린다 — `_daily_key` 의 docstring 이 경고하는 바로 그 침묵이다. 소스에
    같은 이름이 나타나는지로 본다.
    """
    for func in (limit_assistant, consume_daily):
        assert "_daily_key" in _called_names(func), (
            f"{func.__name__} 이 `_daily_key` 를 쓰지 않습니다 — "
            "키가 갈리면 일일 한도가 조용히 사라집니다."
        )


def test_the_limiter_module_lists_every_route_it_actually_guards():
    """문서화된 근거가 코드와 어긋난 채로 남지 않게 한다.

    이 결함의 원인은 코드가 아니라 **모듈 docstring 의 일반화**였다
    ("막을 것은 `/api/assistant` 하나다"). 그 문장이 되살아나면 다음 사람이
    같은 판단을 반복한다.

    > **첫 판본은 `"/api/search" in header` 였다 — 즉 열거였다.** 이 파일이
    > 스스로 *"대상 목록도 적지 않는다"* 고 적어놓고 마지막 검사에서 리터럴
    > 하나를 박아둔 것이라, **네 번째 LLM 경로가 생기고 설명에서 빠지면 조용히
    > 통과했다.** 지금은 `_llm_routes()` 가 찾아낸 경로 전부를 요구한다.
    >
    > docstring 도 소스를 삼중따옴표로 쪼개 두 번째 조각을 쓰고 있었는데, 그
    > 파일에는 그런 구간이 15개라 두 번째가 모듈 설명인 것은 **순서 덕**이었다.
    > 이제 `__doc__` 을 직접 쓴다.
    """
    missing = _paths_missing_from(rate_limit_module.__doc__ or "")
    assert not missing, (
        "rate_limit.py 의 모듈 설명이 실제 한도 대상을 빠뜨렸습니다: "
        + ", ".join(missing)
        + " — 대상 목록이 코드와 어긋나면 그게 다음 누락의 근거가 됩니다."
    )


def _paths_missing_from(header: str) -> list[str]:
    """모듈 설명에서 빠진 LLM 경로들. **위 검사와 아래 공허 방지가 같은 식을 쓴다.**

    나눠 놓지 않으면 공허 방지가 *다른* 식을 검사하게 된다 — 실제로 첫 판본이
    그랬다(아래 참고).
    """
    return [route.path for route in _llm_routes() if route.path not in header]


def test_that_docstring_check_can_fail():
    """**공허 방지 — 이번엔 진짜로.**

    > 첫 판본은 `assert "/api/does-not-exist" not in header` 였다. 그건 **위
    > 검사가 쓰는 식을 한 번도 실행하지 않는다** — `_llm_routes()` 가 0개를
    > 돌려주거나 판정식이 뒤집혀도 통과한다. *공허를 막겠다는 검사가 공허했다.*
    > 이 파일이 고치겠다고 나선 바로 그 결함이다 (ADR-0049).

    이제 같은 식(`_paths_missing_from`)에 **실제 경로가 빠진 설명**을 넣어,
    빠진 것을 실제로 지목하는지 본다.
    """
    real = rate_limit_module.__doc__ or ""
    assert _paths_missing_from(real) == [], "현행 설명은 통과해야 한다"

    stripped = real.replace("/api/search", "")
    assert "/api/search" in [r.path for r in _llm_routes()], (
        "표본이 실제 LLM 경로가 아니면 이 검사는 아무것도 구별하지 못한다"
    )
    assert _paths_missing_from(stripped) == ["/api/search"]
