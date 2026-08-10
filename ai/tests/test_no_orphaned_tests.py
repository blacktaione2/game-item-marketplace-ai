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

**pytest 가 조용히 건너뛰는 모양이 없어야 한다.** 네 가지다.

1. `test_*` 가 다른 **함수 안에 중첩** — ADR-0049 가 겪은 그 모양
2. `Test*` 클래스에 **`__init__` 이 있음** — 클래스 통째로 수집에서 빠진다 (경고 하나)
3. 같은 모듈에 **같은 이름이 두 번** — 뒤가 앞을 덮는다 (무신호)
4. 클래스 이름이 **`Test` 로 시작하지 않음** — 메서드가 통째로 안 돈다 (무신호)

### 처음에는 1번만 막았다

ADR-0049 는 사고가 난 축에만 가드를 달았다. 그런데 이 저장소는 **바로 그 라운드
직전에** 반대 규칙을 적어뒀다 — *"가드는 사고가 난 축이 아니라 성질에 붙인다.
사고는 한 축에서 나지만 성질은 여러 축에 걸친다"* (ADR-0047, `load/run.sh` 의
`SUITE`/`MODE`). 여기서 성질은 **"`test_*` 인데 pytest 가 안 돈다"** 이고, 축은
셋이다.

### 4번은 목록에 있었는데 「정상」 표본에 들어가 있었다 (ADR-0050 정정)

사고 직후 만든 목록에는 **네 모양**이 있었다. 그런데 가드는 셋만 보고, 남은 하나는
*지목하면 안 되는 모양*으로 **반대편 표본**(`test_normal_shapes_are_not_flagged`)에
들어갔다. **빠뜨린 게 아니라 의도된 동작으로 적어둔 것**이라 더 나쁘다 — 다음
사람이 그 표본을 근거로 "그건 일부러 안 잡는 것"이라고 읽는다.

재보니 4번은 **가장 조용하다**: `class Helper:` 안의 `assert False` 짜리 테스트가
`1 passed`, exit 0, 경고조차 없다. 지금 저장소에 0건이고, 오탐 위험은 낮다 —
해소가 "클래스명을 바꾸거나 메서드에서 `test_` 를 뗀다"로 싸다.

### 그리고 다섯째 후보는 재보고 뺐다 (ADR-0050)

`async def test_` 도 같은 계열로 보였다. 이 저장소에는 `pytest-asyncio` 가 없고
CLAUDE.md 가 *"몇 안 되는 비동기 경우는 `asyncio.run` 을 쓴다"* 고 적어둬서, 누군가
`async def test_` 를 쓸 유인이 분명히 있다.

**실제로 돌려보니 조용하지 않았다** — pytest 9.1.1 은 이걸 **실패**로 낸다
(*"async def functions are not natively supported"*, exit 1). 조용한 손실이
아니므로 뺐다. 반대로 `__init__` 과 중복 이름은 **둘 다 exit 0 으로 통과했다**
(경고 하나, 그리고 아무것도) — 그래서 넣었다. *조용한지 아닌지는 짐작하지 말고
돌려본다.*

## 없는 테스트를 가리키는 것도 본다

같은 파일이 **소스 주석이 존재하지 않는 테스트 파일을 가리키는지**도 검사한다.
ADR-0049 가 고친 결함 중 하나가 *"javadoc 이 없는 테스트 이름을 가리킨다"* 였는데,
**그 라운드가 만든 파일에서 같은 것이 재발했다.** 가리키는 쪽과 가리켜지는 쪽이
어긋나면, 다음 사람은 없는 보호막을 믿는다.

**`.md` 는 대상이 아니다** — 이유는 `_SCAN_SUFFIXES` 옆에 적었다.

수집 목록을 pytest 에서 받아 대조하는 방법도 있지만, 그러려면 테스트 안에서
pytest 를 다시 돌려야 한다. AST 만으로 같은 결함을 잡을 수 있으면 그쪽이 싸다 —
**이 저장소의 검사는 네트워크 없이 도는 것이 규칙**이기도 하다.
"""

from __future__ import annotations

import ast
import os
import pathlib
import re
from collections import Counter

TESTS_DIR = pathlib.Path(__file__).parent
AI_ROOT = TESTS_DIR.parent
REPO_ROOT = AI_ROOT.parent

_FUNC = (ast.FunctionDef, ast.AsyncFunctionDef)


def _uncollected(tree: ast.AST) -> list[str]:
    """pytest 가 **수집하지 않는** `test_*` 들. 세 축을 한 함수에서 본다.

    한 함수인 이유는 성질이 하나이기 때문이다 — *"이름은 테스트인데 안 돈다"*.
    축마다 함수를 나누면 다음 축을 추가할 때 호출부를 또 고쳐야 하고, 그
    "또 고쳐야 하는 자리"가 이 저장소에서 계속 새는 것이다.
    """
    found: list[str] = []

    # 1. 다른 *함수* 안에 중첩. 클래스 안(`TestX.test_y`)은 정상이다.
    def walk(node: ast.AST, inside_function: bool) -> None:
        for child in ast.iter_child_nodes(node):
            is_func = isinstance(child, _FUNC)
            if is_func and child.name.startswith("test_") and inside_function:
                found.append(f"{child.name} (줄 {child.lineno}, 함수 안에 중첩)")
            walk(child, inside_function or is_func)

    walk(tree, False)

    for node in ast.iter_child_nodes(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        methods = [
            m.name for m in node.body
            if isinstance(m, _FUNC) and m.name.startswith("test_")
        ]
        if not methods:
            continue
        # 2. `Test*` 클래스에 `__init__` 이 있으면 **클래스 통째로** 빠진다.
        #    pytest 는 `PytestCollectionWarning` 만 내고 exit 0 으로 통과한다.
        if node.name.startswith("Test"):
            if any(isinstance(m, _FUNC) and m.name == "__init__" for m in node.body):
                found.append(
                    f"{node.name} (줄 {node.lineno}, __init__ 때문에 "
                    f"{len(methods)}건 수집 안 됨)"
                )
        # 4. 클래스 이름이 `Test` 로 시작하지 않으면 **아무 신호 없이** 안 돈다.
        #    경고조차 없다 — `__init__` 쪽보다 더 조용하다(실측).
        else:
            found.append(
                f"{node.name} (줄 {node.lineno}, 클래스 이름이 Test* 가 아니라 "
                f"{len(methods)}건 수집 안 됨 — 클래스명을 바꾸거나 "
                f"메서드 이름에서 test_ 를 떼세요)"
            )

    # 3. 같은 이름이 두 번이면 뒤가 앞을 **덮는다.** 경고조차 없다.
    scopes: list[list[str]] = [
        [n.name for n in ast.iter_child_nodes(tree)
         if isinstance(n, _FUNC) and n.name.startswith("test_")]
    ]
    scopes += [
        [m.name for m in node.body if isinstance(m, _FUNC)
         and m.name.startswith("test_")]
        for node in ast.iter_child_nodes(tree)
        if isinstance(node, ast.ClassDef)
    ]
    for names in scopes:
        for name, count in Counter(names).items():
            if count > 1:
                found.append(f"{name} (같은 이름 {count}번 — 앞의 정의가 덮인다)")

    return found


def _test_files() -> list[pathlib.Path]:
    return sorted(TESTS_DIR.glob("test_*.py"))


def test_there_is_something_to_check():
    """파일을 0개로 세면 아래 검사는 공짜로 통과한다."""
    files = _test_files()
    assert len(files) >= 10, f"테스트 파일을 못 찾았습니다: {files}"
    assert any(p.name == "test_llm_route_metering.py" for p in files)


def test_no_test_is_silently_uncollected():
    missed = []
    for path in _test_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        missed += [f"{path.name}::{name}" for name in _uncollected(tree)]
    assert not missed, (
        "pytest 가 **수집하지 않는** 테스트가 있습니다: "
        + ", ".join(missed)
        + " — 개수만 보면 이 손실은 다른 추가에 가려집니다."
    )


def test_the_scan_catches_the_nesting_shape():
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
    assert [n.split(" ")[0] for n in _uncollected(broken)] == ["test_swallowed"]


def test_the_scan_catches_a_constructor_in_a_test_class():
    """**실측으로 넣은 축.** pytest 는 경고 하나만 내고 **exit 0** 으로 통과한다.

    직접 돌려본 결과(ADR-0050): `TestWithInit.test_never_collected` 이
    `assert False` 인데도 `2 passed, 1 warning`.
    """
    broken = ast.parse(
        "class TestWithInit:\n"
        "    def __init__(self):\n"
        "        self.x = 1\n"
        "\n"
        "    def test_never_collected(self):\n"
        "        assert False\n"
    )
    assert [n.split(" ")[0] for n in _uncollected(broken)] == ["TestWithInit"]


def test_the_scan_catches_a_class_not_named_test():
    """**네 번째 축 — 처음엔 이걸 「정상」 표본에 넣어뒀다** (ADR-0050 정정).

    사고 직후 만든 목록에는 네 모양이 있었는데, 가드는 셋만 봤고 나머지 하나는
    *지목하면 안 되는 모양*으로 반대편 표본에 들어가 있었다. **빠뜨린 게 아니라
    의도된 동작으로 적어둔 것**이라 더 나쁘다.

    실측: `class Helper:` 안의 `assert False` 짜리 `test_` 는 `1 passed`, exit 0,
    **경고조차 없다** — `__init__` 축(경고 하나)보다 조용하다.
    """
    broken = ast.parse(
        "class Helper:\n"
        "    def test_never_collected(self):\n"
        "        assert False\n"
    )
    assert [n.split(" ")[0] for n in _uncollected(broken)] == ["Helper"]


def test_the_scan_catches_a_duplicate_name():
    """**두 번째 실측 축.** 이건 경고조차 없다 — 뒤 정의가 앞을 덮을 뿐이다."""
    broken = ast.parse(
        "def test_same():\n"
        "    assert False\n"
        "\n"
        "def test_same():\n"
        "    assert True\n"
    )
    assert [n.split(" ")[0] for n in _uncollected(broken)] == ["test_same"]


def test_normal_shapes_are_not_flagged():
    """**반대 방향 공허 방지.** 정상 모양을 결함으로 지목하면 못 쓴다.

    특히 `__init__` 축은 헛발 위험이 있다 — `Test*` 가 아닌 헬퍼 클래스나
    생성자만 있고 테스트가 없는 클래스를 지목하면 멀쩡한 코드를 고치게 된다
    (사례 31 이 그런 종류였다).
    """
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
        "\n"
        "class TestFixtureHolder:\n"
        "    def __init__(self):\n"
        "        self.x = 1\n"
        "\n"
        "class Helper:\n"
        "    def __init__(self):\n"
        "        self.x = 1\n"
        "\n"
        "    def build(self):\n"
        "        return 1\n"
        "\n"
        "class TestA:\n"
        "    def test_shared_name(self):\n"
        "        assert True\n"
        "\n"
        "class TestB:\n"
        "    def test_shared_name(self):\n"
        "        assert True\n"
    )
    assert _uncollected(fine) == []


# --- 없는 테스트를 가리키는 글 ------------------------------------------------

_TEST_FILE_REF = re.compile(r"\b(test_[A-Za-z0-9_]+\.py|[A-Za-z0-9_]*Test\.java)\b")

#: **소스만 본다. `.md` 는 일부러 뺐다.**
#:
#: 결함은 *"주석이 있지도 않은 가드를 약속했다"* 였다 — 코드를 읽는 사람이 없는
#: 보호막을 믿는 것. 반면 ADR·트러블슈팅 문서는 **틀렸던 이름을 그대로 인용해야
#: 하는 자리**다(이 저장소의 ADR 은 덮어쓰지 않고 정정을 덧붙인다). 문서까지
#: 훑으면 사고를 기록하는 문장이 곧 위반이 되고, 그건 **멀쩡한 글을 고치게 만드는
#: 오탐**이다 — 사례 31 이 "공허한 통과보다 나쁠 수 있다"고 적은 방향.
_SCAN_SUFFIXES = {".py", ".java", ".ts", ".tsx", ".sh", ".yml"}
_SCAN_SKIP = {".git", "node_modules", ".venv", "__pycache__", "dist", "build",
              "models", ".gradle", "out", ".pytest_cache"}


#: 이 파일은 **일부러 없는 이름**을 표본으로 들고 있어야 한다(아래 공허 방지).
#: 그래서 자기 자신은 전수 검사에서 빼되, **면제를 전면적으로 주지 않는다** —
#: 이 파일에 있어도 되는 것은 정확히 이 이름 하나뿐이고 그것도 아래에서 단언한다.
_SAMPLE_DEAD_NAME = "test_collected_tests.py"


def _walk_repo() -> list[pathlib.Path]:
    """`node_modules`·`.venv` 로 **내려가지 않는다.**

    `rglob("*")` 로 짰더니 스위트가 32초에서 106초가 됐다. 가지를 쳐야지
    다 걸어놓고 거르면 안 된다 — 걸어 들어가는 것 자체가 비용이다.
    """
    found: list[pathlib.Path] = []
    for dirpath, dirnames, filenames in os.walk(REPO_ROOT):
        dirnames[:] = [d for d in dirnames if d not in _SCAN_SKIP]
        base = pathlib.Path(dirpath)
        found += [base / name for name in filenames]
    return found


def _existing_test_filenames() -> set[str]:
    return {p.name for p in _walk_repo() if _TEST_FILE_REF.fullmatch(p.name)}


def _dangling_refs(text: str, existing: set[str]) -> list[str]:
    """글에 적힌 테스트 파일 이름 중 **실제로 없는 것.**

    아래 검사와 공허 방지가 같은 식을 쓴다 (ADR-0049 가 배운 것).
    """
    return sorted({m for m in _TEST_FILE_REF.findall(text) if m not in existing})


def _scan_targets() -> list[pathlib.Path]:
    return [p for p in _walk_repo() if p.suffix in _SCAN_SUFFIXES]


def test_nothing_points_at_a_test_file_that_does_not_exist():
    """**가리키는 쪽과 가리켜지는 쪽이 어긋나면 없는 보호막을 믿게 된다.**

    ADR-0049 가 고친 것과 같은 결함이 **그 라운드가 만든 파일에서** 재발했다 —
    `test_llm_route_metering.py` 의 주석이 `test_collected_tests.py` 를
    가리켰는데, 그런 파일은 없다(실제 이름은 이 파일이다). 사람이 읽고
    "아 그게 막아주는구나" 하고 넘어가는 종류라 조용하다.
    """
    existing = _existing_test_filenames()
    assert len(existing) > 20, f"표본이 이상합니다 — 찾은 테스트 파일 {len(existing)}개"

    dangling = {}
    for path in _scan_targets():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        missing = _dangling_refs(text, existing)
        if path.name == pathlib.Path(__file__).name:
            # 자기 자신은 표본을 들고 있어야 한다. **면제는 그 이름 하나뿐이고,
            # 그것도 여기서 단언한다** — 통째로 빼면 이 파일만 규칙 밖이 된다.
            assert missing == [_SAMPLE_DEAD_NAME], (
                f"이 파일이 표본 외의 없는 이름을 가리킵니다: {missing}"
            )
            continue
        if missing:
            dangling[path.relative_to(REPO_ROOT).as_posix()] = missing

    assert not dangling, (
        "없는 테스트 파일을 가리키는 글이 있습니다: "
        + "; ".join(f"{k} → {v}" for k, v in sorted(dangling.items()))
    )


def test_the_reference_scan_can_actually_fail():
    """**공허 방지 — 같은 식(`_dangling_refs`)을 실패 방향으로 돌린다.**

    실제로 있었던 이름을 표본으로 쓴다. 지어낸 이름으로 하면 정규식만 시험하고
    "실재하는지 확인하는" 부분은 한 번도 안 돈다.
    """
    existing = _existing_test_filenames()
    real = pathlib.Path(__file__).name
    assert real in existing, "표본이 실재해야 대조가 의미를 갖는다"
    assert _SAMPLE_DEAD_NAME not in existing, "표본이 실재하면 아무것도 구별하지 못한다"

    text = f"이 계열은 `{real}` 와 `{_SAMPLE_DEAD_NAME}` 가 막는다."
    assert _dangling_refs(text, existing) == [_SAMPLE_DEAD_NAME]
