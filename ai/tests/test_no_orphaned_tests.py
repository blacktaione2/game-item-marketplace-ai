"""수집되지 않는 테스트가 있는가.

## 왜 이 파일이 있는가

`test_llm_route_metering.py` 에서 **테스트 하나가 한 라운드 동안 사라져 있었다**
(ADR-0049). 모듈 레벨 헬퍼를 클래스 한가운데에 끼워 넣는 바람에 그 아래 메서드가
헬퍼의 `return` **뒤로 중첩**됐다. 파이썬은 이걸 문법 오류로 보지 않는다 — 그냥
영영 실행되지 않는 안쪽 함수다. pytest 는 수집하지 않고, 아무도 실패하지 않는다.

**그리고 개수로는 못 잡는다.** 그 라운드는 같은 파일에 검사를 셋 더했으므로
파일의 테스트 수가 9 → 11 로 **늘었다.** 저장소·README·발표자료가 세는 총계도
340 → 348 로 늘었다. *순증가가 손실을 가린다* — 이 저장소가 개수를 문서에 적는
습관을 갖고 있어서 특히 그렇다.

## 무엇을 고정하는가

**이름이 `test_` 로 시작하는 함수는 모듈 레벨이거나 클래스의 직접 자식이어야
한다.** 다른 함수 안에 중첩되면 pytest 가 못 보고, 그건 언제나 사고다.

수집 목록을 pytest 에서 받아 대조하는 방법도 있지만, 그러려면 테스트 안에서
pytest 를 다시 돌려야 한다. AST 만으로 같은 결함을 잡을 수 있으면 그쪽이 싸다 —
**이 저장소의 검사는 네트워크 없이 도는 것이 규칙**이기도 하다.
"""

from __future__ import annotations

import ast
import pathlib

TESTS_DIR = pathlib.Path(__file__).parent


def _orphans(tree: ast.AST) -> list[str]:
    """다른 *함수* 안에 중첩된 `test_*` 들.

    클래스 안(`TestX.test_y`)은 정상이고, 함수 안은 사고다.
    """
    found: list[str] = []

    def walk(node: ast.AST, inside_function: bool) -> None:
        for child in ast.iter_child_nodes(node):
            is_func = isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            if is_func and child.name.startswith("test_") and inside_function:
                found.append(f"{child.name} (줄 {child.lineno})")
            walk(child, inside_function or is_func)

    walk(tree, False)
    return found


def _test_files() -> list[pathlib.Path]:
    return sorted(TESTS_DIR.glob("test_*.py"))


def test_there_is_something_to_check():
    """파일을 0개로 세면 아래 검사는 공짜로 통과한다."""
    files = _test_files()
    assert len(files) >= 10, f"테스트 파일을 못 찾았습니다: {files}"
    assert any(p.name == "test_llm_route_metering.py" for p in files)


def test_no_test_is_nested_inside_a_function():
    orphaned = []
    for path in _test_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        orphaned += [f"{path.name}::{name}" for name in _orphans(tree)]
    assert not orphaned, (
        "함수 안에 중첩돼 **수집되지 않는** 테스트가 있습니다: "
        + ", ".join(orphaned)
        + " — 모듈 레벨이나 클래스 직접 자식으로 옮기세요. "
        "개수만 보면 이 손실은 다른 추가에 가려집니다."
    )


def test_the_scan_can_actually_fail():
    """**공허 방지.** 실제로 사라졌던 모양을 그대로 넣어 잡히는지 본다.

    아래는 ADR-0049 가 고친 코드의 축소판이다 — 모듈 레벨 헬퍼 뒤에 클래스
    메서드가 딸려 들어간 모습.
    """
    broken = ast.parse(
        "def _helper():\n"
        "    return {}\n"
        "\n"
        "    def test_swallowed(self):\n"
        "        assert True\n"
    )
    assert [n.split(" ")[0] for n in _orphans(broken)] == ["test_swallowed"]


def test_normal_shapes_are_not_flagged():
    """**반대 방향 공허 방지.** 정상 모양을 결함으로 지목하면 못 쓴다."""
    fine = ast.parse(
        "def test_module_level():\n"
        "    assert True\n"
        "\n"
        "class TestGroup:\n"
        "    def test_method(self):\n"
        "        assert True\n"
        "\n"
        "def test_with_an_inner_sample():\n"
        "    def helper_not_a_test():\n"
        "        return 1\n"
        "    assert helper_not_a_test() == 1\n"
    )
    assert _orphans(fine) == []
