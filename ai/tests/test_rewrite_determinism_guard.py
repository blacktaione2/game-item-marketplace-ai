"""결정성 하네스가 **실패를 결정성으로 세지 않는가**.

## 왜 이 파일이 있는가

`evaluate_rewrite_determinism.py` 는 손으로만 도는 스크립트다. 그래서
`test_ir_gate.py` 와 같은 처지다 — **평소에 아무도 안 돌리는 판정 코드**라서,
틀려도 다음 측정 때까지 아무도 모른다.

고친 결함은 이것이다. `understand_query` 는 LLM 호출이 실패하면 예외 대신
`rewritten_query=원본질의`, 빈 필터, `degraded=True` 로 **폴백**한다. 그 폴백은
매번 **글자까지 같다.** 그래서 폴백을 안 세면

    10회 중 4회가 429 로 죽으면 → 그 4회가 서로 완전히 일치 → **일치율이 오른다**

즉 **장애가 "더 결정적"으로 보인다.** ADR-0017 의 0.840→0.990 과 ADR-0045 의
0.950/0.920 이 이 하네스의 출력이다.

사례 19(실패를 `is not False` 로 세어 미검출률이 63% 로 뛴 건)의 거울상이고,
처방도 같다 — **분모에서 빼고, 실패가 조금이라도 많으면 아예 채점하지 않는다.**

## 규칙

`test_ir_gate.py` 와 같다: **통과하는 경우마다 실패하는 짝을 둔다.** 통과만 본
검사는 항상 통과하는 검사와 구별되지 않는다.
"""

from __future__ import annotations

import io
from contextlib import redirect_stdout

from scripts.evaluate_rewrite_determinism import (
    MAX_DEGRADED_RATE,
    MIN_VALID_FRACTION_PER_QUERY,
    report,
)


def _row(query: str, good_runs: int, degraded: int, *, distinct: int = 1) -> dict:
    """`measure()` 가 내는 모양의 합성 행.

    `distinct` 가 1이면 남은 표본이 완벽히 일치한다 — 옛 판본이라면 폴백이
    몇 개든 "일치율 1.00" 을 보고했을 모양이다.
    """
    values = [f'{{"v":{i % distinct}}}' for i in range(good_runs)]
    return {
        "query": query,
        "filters": values,
        "tokens": values,
        "exact": values,
        "degraded": degraded,
        "runs": good_runs + degraded,
    }


def _run(rows: list[dict]) -> tuple[bool, str]:
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        scored = report(rows, 10, 0.0)
    return scored, buffer.getvalue()


class TestItRefusesToScoreOnFailures:
    def test_폴백이_절반이면_채점하지_않는다(self):
        """**결함 표본.** 남은 5회가 완벽히 일치하므로 옛 판본은 만점을 줬다."""
        scored, output = _run([_row("검 찾아줘", good_runs=5, degraded=5)])
        assert scored is False
        assert "채점하지 않는다" in output

    def test_전부_폴백이면_채점하지_않는다(self):
        scored, _ = _run([_row("검 찾아줘", good_runs=0, degraded=10)])
        assert scored is False

    def test_정상_표본은_채점한다(self):
        """**공허 방지.** 가드가 전부 거부하면 하네스가 죽은 것과 같다."""
        scored, output = _run([_row("검 찾아줘", good_runs=10, degraded=0)])
        assert scored is True
        assert "폴백(degraded) 0/10" in output

    def test_상한_이하의_폴백은_통과한다(self):
        """경계로 잰다 — "폴백이 있으면 무조건 거부"면 정상 실행이 막힌다.

        > **첫 판본은 여기서 질의 20건을 썼다** (ADR-0049). 실제 `FLOOR_QUERIES`
        > 는 **10건**이라, 존재하지 않는 규모에서 통과를 승인하고 있었다.
        > 실제 규모로 맞췄다 — 검사가 승인하는 상황은 **일어날 수 있는 상황**이어야
        > 한다.
        """
        rows = [_row(f"q{i}", good_runs=10, degraded=0) for i in range(9)]
        rows.append(_row("q9", good_runs=8, degraded=2))  # 전체 2%, 질의별 80%
        scored, _ = _run(rows)
        assert scored is True

    def test_한_질의만_망가져도_채점하지_않는다(self):
        """**전체 비율은 한 질의의 붕괴를 희석한다.**

        실제 규모(10건 × 10회)에서 한 질의가 5회 폴백이면 전체는 **정확히
        5.0%** 라 전역 상한을 통과한다. 그런데 그 질의는 남은 5개로
        `mode_agreement` **1.00** 을 낸다 — 만점이다. 질의별 분모가 없으면
        가드가 여기서 뚫린다.
        """
        rows = [_row(f"q{i}", good_runs=10, degraded=0) for i in range(9)]
        rows.append(_row("q9", good_runs=5, degraded=5))
        total = sum(r["runs"] for r in rows)
        assert sum(r["degraded"] for r in rows) / total <= MAX_DEGRADED_RATE, (
            "표본이 전역 상한을 넘으면 질의별 가드를 시험하지 못한다"
        )
        scored, output = _run(rows)
        assert scored is False
        assert "유효 표본이 모자란" in output

    def test_질의별_최소치가_100퍼센트가_아니다(self):
        """1.0 이면 폴백 한 번에 전체가 거부된다 — 위 경계 검사가 무의미해진다."""
        assert 0.5 <= MIN_VALID_FRACTION_PER_QUERY < 1.0

    def test_상한이_0이_아니다(self):
        """상한이 0이면 위 경계 검사가 의미를 잃는다 — 그것도 고정한다."""
        assert 0 < MAX_DEGRADED_RATE < 0.5


class TestTheFailureIsVisibleInTheOutput:
    def test_판정이_쓴_값이_출력에_있다(self):
        """이 저장소의 규칙 — **판정에 쓴 값을 전부 찍는다.**

        폴백 수를 안 찍으면 장애로 올라간 일치율과 진짜 결정성을 구별할 방법이
        없다. 사례 19 를 잡아낸 것이 정확히 이 습관이었다.
        """
        _, output = _run([_row("검 찾아줘", good_runs=7, degraded=3)])
        assert "폴백(degraded) 3/10" in output
        assert "검 찾아줘" in output
