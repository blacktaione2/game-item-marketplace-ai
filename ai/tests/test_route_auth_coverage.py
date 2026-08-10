"""모든 경로에 인증이 걸려 있는가 (ADR-0033).

## 왜 이 테스트가 있는가

`POST /api/llm/test` 가 **인증 없이 공개 배포에 노출돼 있었다.** 다른 다섯
라우터가 전부 `Depends(require_actor)` 를 달 때 여기만 빠졌고, 그대로
`/api/ai/llm/test` 로 닿아 **토큰 없이 임의 프롬프트를 OpenAI 에 보낼 수** 있었다.
비용 방어 3계층(토큰 요구 · 20회/분 · 50회/일)을 전부 우회한다.

라우터를 지우는 것만으로는 부족하다. **결함의 원인은 그 파일이 아니라 방식이다** —
인증을 경로마다 개별로 붙이면 빠뜨려도 아무 신호가 없다. 새 라우터를 추가하면서
의존성을 잊는 것은 똑같이 조용하다.

그래서 개별 경로가 아니라 **"인증 없는 경로가 존재하지 않는다"** 를 고정한다.
이건 삭제된 엔드포인트 하나가 아니라 **다음 번 누락**을 잡는 검사다.

## 면제 목록에 대해

`_EXEMPT` 는 **의도적으로 열어둔 것만** 담는다. 여기에 뭔가를 추가하는 것은 공개
경로를 하나 더 만든다는 뜻이므로 항목마다 사유가 붙어야 한다. "테스트를
통과시키려고" 추가하면 이 검사는 그 순간 장식이 된다.

## 이 파일이 스스로에 대해 확인하는 것

작성 중에 **본 검사가 경로를 0개로 세어 공짜로 통과**했다. 이 FastAPI 버전은
`include_router` 한 라우터를 `_IncludedRouter` 로 감싸서 `app.routes` 에 평탄화하지
않는데, 그걸 모르고 한 겹만 봤기 때문이다. `test_there_is_something_to_check` 가
없었다면 **항상 통과하는 검사**를 그대로 넣을 뻔했다.
"""

from __future__ import annotations

from fastapi import Depends, FastAPI

from app.core.auth import require_actor, require_admin
from app.main import app

# 인증 없이 열어두는 경로. **각각 사유가 있다.**
#   /health  — docker-compose 헬스체크. 백엔드도 이걸 프록시해서 부른다
#   /metrics — Prometheus 스크레이프. nginx 가 `/api/*` 만 넘기므로 공개 노출은 없다
_EXEMPT = {"/health", "/metrics"}

_AUTH_DEPENDENCIES = {require_actor, require_admin}


def _walk(routes):
    """`_IncludedRouter` 안쪽까지 내려가 실제 라우트를 전부 낸다.

    `app.routes` 를 한 겹만 보면 포함된 라우터가 안 보인다 — 이 파일 docstring 의
    "공짜로 통과" 가 바로 그것이었다.
    """
    for route in routes:
        included = getattr(route, "original_router", None)
        if included is not None:
            yield from _walk(included.routes)
        else:
            yield route


def dependency_callables(route, *, max_depth: int | None = None) -> set:
    """이 경로가 의존하는 호출 가능 객체를 **끝까지** 모은다.

    `limit_assistant` 처럼 `require_actor` 를 품은 의존성이 있어서 한 겹으로는
    부족하다. 예전 판본은 **정확히 두 겹**만 봤고, 실측상 지금 트리의 최대 깊이도
    2 라 여유가 0이었다 — 누가 의존성을 하나 더 감싸면 그 순간 조용해진다.

    **그 침묵의 방향이 쓰는 쪽마다 다르다.**

    | 쓰임 | 못 봤을 때 |
    |---|---|
    | *판정* (`limit_assistant` 가 있는가) | 없다고 판정 → **시끄럽게 실패** |
    | *모집단* (`get_llm_client` 를 받는가) | 목록에서 **조용히 빠진다** |

    두 번째가 `test_llm_route_metering.py` 가 쓰는 방식이다. 그래서 깊이를
    적어두는 대신 **없앴다** — 적어둔 한계는 시간이 지나면 면제로 읽힌다.

    `max_depth` 는 공허 방지가 옛 동작을 재현하려고만 쓴다.
    """
    found = set()

    def visit(dependencies, level: int) -> None:
        if max_depth is not None and level > max_depth:
            return
        for dependency in dependencies:
            found.add(dependency.call)
            visit(dependency.dependencies, level + 1)

    visit(route.dependant.dependencies, 1)
    return found


def _guarded_routes():
    """인증이 걸려 있어야 하는 라우트. FastAPI 자체 스키마 경로는 제외한다."""
    return [
        route
        for route in _walk(app.routes)
        if hasattr(route, "dependant") and route.path not in _EXEMPT
    ]


class TestEveryRouteRequiresAuth:
    def test_there_is_something_to_check(self):
        """경로가 0개면 아래 검사는 공짜로 통과한다 — 그걸 먼저 막는다."""
        paths = {r.path for r in _guarded_routes()}
        assert len(paths) >= 5, f"라우트를 못 찾았습니다: {paths}"
        # 실제로 아는 경로가 잡히는지도 본다. 개수만 세면 엉뚱한 걸 세도 통과한다.
        assert "/api/assistant" in paths
        assert "/api/anomaly/alerts" in paths

    def test_every_route_has_an_auth_dependency(self):
        unprotected = [
            f"{sorted(route.methods)} {route.path}"
            for route in _guarded_routes()
            if not (dependency_callables(route) & _AUTH_DEPENDENCIES)
        ]
        assert not unprotected, (
            "인증 의존성이 없는 경로가 있습니다: "
            + ", ".join(unprotected)
            + " — 의도한 것이면 _EXEMPT 에 사유와 함께 추가하세요."
        )

    def test_admin_queue_requires_admin_specifically(self):
        """`require_actor` 만으로는 부족한 유일한 경로. 일반 토큰으로 열리면 안 된다."""
        alerts = [r for r in _guarded_routes() if r.path == "/api/anomaly/alerts"]
        assert alerts, "GM 큐 경로가 사라졌습니다"
        assert require_admin in dependency_callables(alerts[0])

    def test_a_deeply_nested_dependency_is_still_found(self):
        """**옛 깊이 제한을 인자로 재현해 나란히 돌린다** (ADR-0057).

        `max_depth=2` 가 예전 동작이다. 세 겹짜리 의존성을 만들어 **옛 판본은
        못 보고 지금 판본은 본다**를 한 검사에서 확인한다 — 표본만 만들고 본
        식에 안 먹이면 사례 48 과 같은 실수가 된다.

        이 표본이 중요한 이유는 **못 봤을 때의 방향** 때문이다. 인증 쪽에서는
        못 보면 "인증 없음" 으로 시끄럽게 실패하지만,
        `test_llm_route_metering.py` 는 같은 함수로 **모집단**을 정한다 — 거기서
        못 보면 라우트가 목록에서 조용히 빠진다.
        """
        probe = FastAPI()

        def innermost(actor=Depends(require_actor)):
            return actor

        def middle(x=Depends(innermost)):
            return x

        def outermost(y=Depends(middle)):
            return y

        @probe.post("/deep")
        async def _deep(z=Depends(outermost)):  # pragma: no cover - 표본
            return {}

        route = next(r for r in probe.routes if getattr(r, "path", "") == "/deep")
        assert require_actor not in dependency_callables(route, max_depth=2), (
            "옛 판본이 이 표본을 이미 봤다면 이 검사는 아무것도 구별하지 못한다"
        )
        assert require_actor in dependency_callables(route)

    def test_the_shallow_case_did_not_need_the_recursion(self):
        """**반대 방향.** 지금 실제 라우트는 두 겹 안에 다 들어온다.

        재귀가 *없던* 동작까지 바꿔버렸으면 이 검사가 걸린다 — 넓힌 검사가
        엉뚱한 것을 새로 쓸어 담지 않는지 본다(사례 31).
        """
        for route in _guarded_routes():
            assert dependency_callables(route) == dependency_callables(
                route, max_depth=2
            ), f"{route.path} 의 의존성 깊이가 2를 넘었습니다 — 실측을 갱신하세요"

    def test_removed_llm_route_is_gone(self):
        """제거된 경로가 되살아나지 않았는지.

        위 검사만으로는 부족하다 — 누군가 이 라우터를 되살리면서 인증을 붙이면
        통과하지만, 그건 **필요 없는 LLM 직결 경로를 다시 만든 것**이다.
        `/health` 가 연동 확인 역할을 대신한다.
        """
        assert not [r for r in _walk(app.routes) if getattr(r, "path", "") == "/api/llm/test"]
