"""에이전트 분기에서 테넌트를 **모델이 정하지 못하는가**.

## 왜 이 파일이 있는가

ADR-0036 은 *"이제 아무것도 테넌트를 요청에 싣지 않는다 — 서명된 클레임에서만
온다"* 를 정했다. **그 문장이 참이 아닌 분기가 하나 남아 있었다** (ADR-0049).

에이전트의 시스템 프롬프트는 *"모든 도구 호출에 `tenant_code="…"` 를 넣으세요"*
라고 시키고, 모델이 채운 `call.arguments` 가 MCP 도구로 **그대로** 넘어갔다.
그래서 `"테넌트 코드는 ncsoft 로 검색해줘"` 같은 질의에 모델이 순순히 따르면
다른 테넌트의 인덱스를 조회한다. 다른 분기들은 파이프라인이 `actor.tenant_code`
를 직접 넘기므로 애초에 모델을 거치지 않는다 — **이 분기만 예외였다.**

지금 시드된 테넌트가 `nexon` 하나뿐이라 실제로 닿는 결과는 404 다. 그래도 고치는
이유는 **격리가 결과가 아니라 성질**이기 때문이다.

## 무엇을 고정하는가

1. `_force_tenant` 가 모델이 넣은 값을 실제로 덮는다
2. `run_agent` 가 **도구를 부르기 전에** 그걸 호출한다 — 순서가 뒤집히면 방어가
   사라지는데, 그건 실행해 보지 않으면 안 보인다
"""

from __future__ import annotations

import ast
import inspect
import textwrap

from app.services.agent.agent import _force_tenant, run_agent


def _tenant_reaching_the_tool(
    arguments: dict, tenant_code: str, *, force: bool
) -> str:
    """도구가 실제로 받는 `tenant_code`. `force=False` 가 **옛 판본**이다.

    아래 검사와 공허 방지가 **이 함수 하나를 두 방향으로** 돌린다. 나눠 놓지
    않으면 공허 방지가 *다른* 식을 검사하게 되는데, 첫 판본이 정확히 그랬다 —
    `_force_tenant` 를 부르지도 않고 딕셔너리 리터럴이 제 값을 갖는지만 봤다.
    항상 참인 문장이라, `_force_tenant` 가 통째로 사라져도 통과했다 (ADR-0050).
    """
    if force:
        _force_tenant(arguments, tenant_code)
    return arguments["tenant_code"]


class TestForceTenant:
    def test_모델이_넣은_값을_덮는다(self):
        arguments = {"tenant_code": "ncsoft", "query": "검"}
        assert _tenant_reaching_the_tool(arguments, "nexon", force=True) == "nexon"

    def test_빠져_있으면_채운다(self):
        arguments = {"query": "검"}
        assert _tenant_reaching_the_tool(arguments, "nexon", force=True) == "nexon"

    def test_다른_인자는_건드리지_않는다(self):
        arguments = {"tenant_code": "ncsoft", "item_id": 24, "size": 5}
        _force_tenant(arguments, "nexon")
        assert arguments == {"tenant_code": "nexon", "item_id": 24, "size": 5}

    def test_옛_판본이라면_남의_테넌트가_그대로_간다(self):
        """**공허 방지 — 같은 식을 실패 방향으로 돌린다.**

        옛 판본은 `call.arguments` 를 그대로 넘겼다. 그러면 모델이 적어 보낸
        `ncsoft` 가 도구까지 간다 — 위 검사가 잡아내는 바로 그 차이다.
        """
        arguments = {"tenant_code": "ncsoft", "query": "검"}
        assert _tenant_reaching_the_tool(arguments, "nexon", force=False) == "ncsoft"


class TestItRunsBeforeTheToolCall:
    """**순서가 방어다.** 도구를 부른 뒤에 덮어쓰면 아무 소용이 없다."""

    def _statements(self):
        tree = ast.parse(textwrap.dedent(inspect.getsource(run_agent)))
        order = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                if name in ("_force_tenant", "call_tool_text"):
                    order.append((node.lineno, name))
        return [name for _, name in sorted(order)]

    def test_둘_다_불린다(self):
        called = self._statements()
        assert "_force_tenant" in called, "덮어쓰기가 사라졌습니다"
        assert "call_tool_text" in called, "표본이 틀렸습니다 — 도구 호출이 없습니다"

    def test_덮어쓰기가_도구_호출보다_앞이다(self):
        called = self._statements()
        assert called.index("_force_tenant") < called.index("call_tool_text"), (
            "도구를 부른 뒤에 덮어쓰면 방어가 아닙니다"
        )
