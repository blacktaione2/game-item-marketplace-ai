"""공허 방지가 **본 검사의 식을 실제로 쓰는가.**

## 왜 이 파일이 있는가

같은 실수가 세 번 났다 (ADR-0056).

| 사례 | 무엇이 |
|---|---|
| 36 | `_force_tenant` 를 부르지도 않는 **항등식**이었다 |
| 44 | `x in (S ∪ {x})` 처럼 **늘 참인 문장**이었다 |
| 48 | 표본은 만들고 **본 검사의 비교식에 안 먹였다** |

세 번 다 검사를 *쓰는 중*에 났다 — 규칙이 필요한 순간이 규칙을 떠올리기 가장
어려운 순간이다. 그래서 **세 번째 뒤에는 사람이 아니라 코드가 묻게 한다.**

## 무엇을 보는가 — 그리고 그 한계

공허 방지가 **같은 파일의 본 검사와 이름을 하나라도 공유하는가.** 공유 대상은
`app` 에서 온 것이거나 **그 테스트 모듈이 스스로 정의한 것**(모듈 함수·클래스·
메서드·상수)이어야 한다.

**깊이: 참조이지 실행이 아니다.** 이 검사는 *"본 검사의 술어를 실패 방향으로
돌렸다"* 를 증명하지 못한다. 증명하는 것은 그보다 약한 명제 —
*"공허 방지가 이 저장소의 무언가를 붙들고 있다"* 다. 그래도 값이 있는 이유는
세 사례가 전부 **아무것도 안 붙들고 있었기** 때문이다.

## 느슨한 규칙은 기각했다 — 재봤다

처음엔 *"이름을 하나라도 공유하면 통과"* 로 짰다. **사례 48 을 못 잡는다** —
그 공허 방지는 본 검사의 로직을 다시 구현했으므로 `ast.walk` · `ExceptHandler`
같은 **표준 라이브러리 이름**을 잔뜩 공유한다. 재구성해서 확인했다:

    느슨: 공유 = ['ExceptHandler', 'handler', 'keyword', 'parse', 'walk', ...] -> 통과
    엄격: 공유 = []                                                            -> 지목

*공유하는 이름이 저장소의 것이어야 한다*가 그 차이다.
"""

from __future__ import annotations

import ast
import pathlib
import textwrap

TESTS_DIR = pathlib.Path(__file__).parent

#: 스스로 공허 방지라고 밝힌 검사를 고르는 표식.
#:
#: **이름과 docstring 을 둘 다 본다.** 이 저장소의 공허 방지는 한국어 이름과
#: 영어 이름이 섞여 있고(`test_옛_판본이라면_...`, `test_..._can_actually_fail`),
#: 한쪽만 보면 절반이 대상에서 빠진다.
_MARKS = ("공허 방지", "can_actually_fail", "can_fail", "실패 표본")


def referenced(node: ast.AST) -> set[str]:
    """이 함수가 참조하는 이름 — 호출·속성·이름 로드를 다 모은다.

    `_LEAK.search(...)` 의 `_LEAK`, `self._both(...)` 의 `_both`, 그냥 쓰는
    상수까지 잡아야 한다. 호출만 세면 정규식 상수를 쓰는 공허 방지를 헛되이
    지목한다(실측: 처음 짠 판본이 `test_error_detail_leak.py` 를 오탐했다).
    """
    found: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            found.add(child.id)
        elif isinstance(child, ast.Attribute):
            found.add(child.attr)
    return found


#: 저장소의 것으로 치는 import 출처.
#:
#: **`tests` 가 여기 있는 이유가 실측이다** (ADR-0057). 처음엔 `app` 만 봤는데,
#: 그러면 **형제 테스트 모듈에서 술어를 가져다 쓰는 공허 방지를 지목한다** —
#: 즉 이 감사가 권하는 바로 그 모양(*"술어에 이름을 붙여 양쪽이 같은 것을
#: 부르게 하라"*)을 결함으로 읽는다. 재현해서 확인했고, 아래
#: `test_a_guard_sharing_a_sibling_test_helper_is_not_flagged` 가 고정한다.
#:
#: 오탐은 공허한 통과보다 나쁠 수 있다 — 사람이 **멀쩡한 코드를 고치게** 만든다.
_REPO_MODULES = ("app", "tests")


def repo_names(tree: ast.Module) -> set[str]:
    """그 테스트 모듈이 **스스로 정의했거나 저장소에서 가져온** 이름.

    클래스 안의 메서드도 넣는다 — `self._except_names(...)` 처럼 클래스 헬퍼를
    공유하는 공허 방지가 있고, 빼면 오탐한다(실측: 2건).
    """
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        if isinstance(node, ast.ClassDef):
            names |= {
                member.name
                for member in node.body
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
        elif isinstance(node, ast.Assign):
            names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
            _REPO_MODULES
        ):
            names |= {a.asname or a.name for a in node.names}
        elif isinstance(node, ast.Import):
            names |= {
                (a.asname or a.name).split(".")[0]
                for a in node.names
                if a.name.startswith(_REPO_MODULES)
            }
    return names


def _is_guard(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    doc = ast.get_docstring(fn) or ""
    return any(mark in doc or mark in fn.name for mark in _MARKS)


def unanchored_guards(tree: ast.Module) -> list[str]:
    """본 검사와 **저장소의 이름을 하나도 공유하지 않는** 공허 방지들.

    본 검사와 공허 방지가 이 식을 공유한다 — 아래 실패 표본도 같은 함수를 쓴다.
    """
    functions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    ]
    guards = [fn for fn in functions if _is_guard(fn)]
    others = [fn for fn in functions if not _is_guard(fn)]
    if not guards:
        return []
    pool: set[str] = set()
    for fn in others:
        pool |= referenced(fn)
    local = repo_names(tree)
    return [fn.name for fn in guards if not (referenced(fn) & pool & local)]


def _trees() -> dict[str, ast.Module]:
    return {
        path.name: ast.parse(path.read_text(encoding="utf-8"))
        for path in sorted(TESTS_DIR.glob("test_*.py"))
    }


def test_there_are_guards_to_audit():
    """공허 방지를 0개로 세면 아래 검사는 공짜로 통과한다."""
    total = sum(
        1
        for tree in _trees().values()
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
        and _is_guard(node)
    )
    assert total >= 15, f"공허 방지를 {total}건밖에 못 찾았습니다 — 표식이 바뀌었습니까?"


def test_every_guard_is_anchored_to_the_real_check():
    unanchored = [
        f"{name}::{guard}"
        for name, tree in _trees().items()
        for guard in unanchored_guards(tree)
    ]
    assert not unanchored, (
        "본 검사와 저장소의 이름을 하나도 공유하지 않는 공허 방지가 있습니다: "
        + ", ".join(unanchored)
        + " — 판정식을 함수로 빼서 양쪽이 같은 것을 부르게 하세요 "
        "(사례 36·44·48)."
    )


def test_the_audit_catches_a_reimplemented_guard():
    """**실패 표본 — 사례 48 을 고치기 전 모양 그대로다.**

    공허 방지가 본 검사의 로직을 **다시 구현**하면 표준 라이브러리 이름은 잔뜩
    공유하지만 저장소의 이름은 하나도 안 쓴다. 이 표본이 느슨한 규칙과 엄격한
    규칙을 가르는 자리다.
    """
    broken = ast.parse(textwrap.dedent('''
        import ast
        import builtins


        class TestX:
            def test_main(self):
                import app.routers as routers_pkg

                for handler in ast.walk(ast.parse("")):
                    if isinstance(handler, ast.ExceptHandler):
                        assert not [n for n in [handler.type] if hasattr(builtins, n)]

            def test_guard_can_actually_fail(self):
                """공허 방지."""
                for handler in ast.walk(ast.parse("")):
                    if isinstance(handler, ast.ExceptHandler):
                        assert [handler.type]
    '''))
    assert unanchored_guards(broken) == ["test_guard_can_actually_fail"]


def test_a_guard_anchored_only_by_a_constant_is_not_flagged():
    """**반대 방향 — 오탐이 더 나쁠 수 있다** (사례 31).

    `test_error_detail_leak.py` 의 공허 방지는 함수가 아니라 **모듈 상수**
    (`_LEAK` 정규식)를 본 검사와 공유한다. 호출만 세던 첫 판본이 이걸 헛되이
    지목했다.
    """
    fine = ast.parse(textwrap.dedent('''
        import re

        _LEAK = re.compile("x")


        def test_main():
            assert not _LEAK.findall("소스")

        def test_scan_can_fail():
            """공허 방지."""
            assert _LEAK.search("x")
    '''))
    assert unanchored_guards(fine) == []


def test_a_guard_sharing_a_sibling_test_helper_is_not_flagged():
    """**반대 방향 셋째 — 이 감사가 권하는 모양을 벌하고 있었다** (ADR-0057).

    술어를 형제 테스트 모듈에서 가져다 쓰는 것은 *이 감사가 시키는 바로 그
    처방*이다(사례 36·44·48 의 해법이 "같은 것을 부르게 하라"였다). 그런데
    `app` 만 저장소로 세던 판본은 그 모양을 **지목했다** — 실측으로 확인했고,
    그래서 `_REPO_MODULES` 에 `tests` 가 있다.
    """
    fine = ast.parse(textwrap.dedent('''
        from tests.test_sibling import predicate


        def test_main():
            assert not predicate("진짜")

        def test_guard_can_actually_fail():
            """공허 방지."""
            assert predicate("표본")
    '''))
    assert unanchored_guards(fine) == []


def test_a_guard_sharing_only_a_class_helper_is_not_flagged():
    """**반대 방향 둘째.** 클래스 헬퍼를 공유하는 모양도 정상이다 (실측 오탐 2건)."""
    fine = ast.parse(textwrap.dedent('''
        class TestX:
            def _predicate(self, value):
                return value > 0

            def test_main(self):
                assert self._predicate(1)

            def test_guard_can_actually_fail(self):
                """공허 방지."""
                assert not self._predicate(-1)
    '''))
    assert unanchored_guards(fine) == []
