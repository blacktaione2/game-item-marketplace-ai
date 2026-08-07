"""500 응답이 예외 문자열을 내보내지 않는다 — ADR-0041.

## 왜 소스를 훑는가

`test_route_auth_coverage.py` 와 같은 이유다. **라우터마다 손으로 쓰는 것은 잊으면
조용하다** — 새 엔드포인트를 하나 더할 때 `detail=f"...{e}"` 라고 쓰는 게 가장
자연스럽고, 그러면 아무도 모르는 채로 내부 정보가 새어나간다.

실제로 이 저장소는 **다섯 라우터 중 다섯이 전부** 그렇게 쓰고 있었다. 하나를
고치고 나머지를 안 봤다면, 사례집이 이미 적어둔 *"같은 파일 안에서 같은 실수가
반복됐다"* 를 또 한 번 반복했을 것이다.

## 무엇이 샜나

업스트림 예외 메시지에는 ES 인덱스명·쿼리 DSL·내부 호스트가 섞여 나온다. 백엔드는
`server.error.include-stacktrace: never` 로 같은 자세를 처음부터 취하고 있었고,
AI 서버만 안 맞춰져 있었다 — **한쪽만 선언된 설정은 결정이 아니라 누락이다.**
"""

import io
import re
from pathlib import Path

import pytest

ROUTERS = sorted((Path(__file__).resolve().parents[1] / "app" / "routers").glob("*.py"))

# `detail=` 값 안에 예외 변수를 f-string 으로 끼워 넣은 자리.
_LEAK = re.compile(r"detail\s*=\s*f?\"[^\"]*\{\s*e[\w.]*\s*\}")


def _source(path: Path) -> str:
    return io.open(path, encoding="utf-8").read()


@pytest.mark.parametrize("path", ROUTERS, ids=lambda p: p.name)
def test_no_router_interpolates_the_exception_into_detail(path):
    leaks = _LEAK.findall(_source(path))
    assert leaks == [], f"{path.name} 이 예외 문자열을 응답에 싣는다: {leaks}"


def test_the_scan_actually_finds_this_pattern():
    """**통과하는 것만 본 검사는 늘 통과하는 검사와 구분되지 않는다.**

    정규식이 오타 하나로 아무것도 안 잡게 되면 위 테스트는 영원히 초록이다.
    일부러 틀린 문자열을 넣어 잡히는지 본다.
    """
    assert _LEAK.search('raise HTTPException(status_code=500, detail=f"실패: {e}")')
    assert _LEAK.search('detail=f"검색 실패: {e}" ')


def test_it_does_not_flag_deliberate_str_e_conversions():
    """`detail=str(e)` 는 **다른 이야기다.**

    그 자리들은 도메인 예외(`ForecastModelNotTrainedError` 등)를 4xx/503 으로
    옮기면서 우리가 쓴 메시지를 그대로 내보내는 것이라 내부 정보가 없다. 이
    검사가 그것까지 막으면 쓸모 있는 오류 메시지가 사라진다.
    """
    assert not _LEAK.search("raise HTTPException(status_code=503, detail=str(e))")


def test_every_router_that_catches_broadly_also_logs():
    """예외를 삼키고 일반 메시지만 내보내면 **진단 수단이 같이 사라진다.**

    `core/rate_limit.py` 가 세운 규칙 그대로다 — 열되 기록한다.
    """
    missing = [
        path.name
        for path in ROUTERS
        if "except Exception" in _source(path)
        and "logger.exception" not in _source(path)
    ]
    assert missing == [], f"포괄 예외를 잡으면서 로그를 안 남기는 라우터: {missing}"
