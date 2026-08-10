"""`timings` dict → Prometheus 히스토그램 변환.

계측 지점이 `record_response` 하나뿐이라 여기만 고정하면 파이프라인 전체가
덮인다. 반대로 이 변환이 조용히 틀리면 **부하테스트가 엉뚱한 곳을 지목한다** —
숫자가 나오긴 하므로 눈으로는 안 걸린다.
"""

import ast
import pathlib
import re

from app.core.metrics import (
    _STAGE_BY_KEY,
    _outcome,
    cache_result,
    record_response,
    render,
    stage_for,
)

APP_ROOT = pathlib.Path(__file__).resolve().parents[1] / "app"

#: 표를 정의하는 파일. **표본에서 뺀다** — 넣으면 검사가 순환한다. 표에 키를
#: 넣기만 하면 "소스에 있다"가 되어 통과하므로, 등록을 잊은 것을 못 잡는다.
_DEFINITION = APP_ROOT / "core" / "metrics.py"

#: 파이프라인이 내보내지만 **단계가 아닌** 키. 값은 제외 사유다.
#:
#: **조용히 건너뛰지 않는다** (ADR-0051). 아래 `test_the_exclusion_list_is_live`
#: 가 *"여기 적힌 키가 실제로 소스에 있다"* 를 단언한다 — 이름이 바뀌면 이 제외가
#: 낡은 채로 남고, 낡은 제외는 그 자체로 조용한 사각이다.
_NOT_A_STAGE = {
    "elapsed_ms": "에이전트 분기의 **전체** 소요 시간이다. 단계가 아니라 합계라, "
                  "stage 라벨로 넣으면 다른 단계들과 이중 계상된다.",
}

_KEY = re.compile(r"^[a-z][a-z0-9_]*_ms$")


def _keys_in_source(text: str) -> set[str]:
    """소스에 나오는 `*_ms` 문자열 상수. **본 검사와 공허 방지가 이 식을 공유한다.**"""
    return {
        node.value
        for node in ast.walk(ast.parse(text))
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and _KEY.match(node.value)
    }


def emitted_timing_keys(*, skip: pathlib.Path | None = _DEFINITION) -> set[str]:
    """파이프라인이 실제로 쓰는 `*_ms` 키를 **소스에서 유도한다.**

    예전에는 이 집합이 손으로 적은 목록이었다 (ADR-0052). *"단계를 늘리면
    `_STAGE_BY_KEY` 에 한 줄"* 이라는 규칙을 지키게 하는 검사가, **그 단계
    목록을 손으로 들고 있었다** — 새 키를 표에 안 넣으면 이 목록에도 안 들어가
    므로 검사는 조용히 통과한다. 실제로 그 목록은 `cache_encode_ms`·
    `cache_lookup_ms`(ADR-0025)·`domain_gate_ms`(ADR-0039) **셋을 빠뜨린 채**
    통과하고 있었다.

    깊이: **문자열 상수 스캔**이다. 키를 변수나 f-string 으로 만들면 못 본다.
    지금은 전부 리터럴이고, 그 편이 `_STAGE_BY_KEY` 와 대조할 수 있어서 좋다.
    """
    found: set[str] = set()
    for path in sorted(APP_ROOT.rglob("*.py")):
        if skip is not None and path == skip:
            continue
        found |= _keys_in_source(path.read_text(encoding="utf-8"))
    return found


def counts_by_stage() -> dict[str, float]:
    """현재 레지스트리에서 stage별 관측 횟수를 뽑는다."""
    result: dict[str, float] = {}
    for line in render().decode().splitlines():
        if not line.startswith("ai_stage_duration_seconds_count"):
            continue
        stage = line.split('stage="', 1)[1].split('"', 1)[0]
        result[stage] = result.get(stage, 0.0) + float(line.rsplit(" ", 1)[1])
    return result


class TestStageMapping:
    def test_there_are_keys_to_check(self):
        """유도가 0개를 내면 아래 검사는 공짜로 통과한다."""
        emitted = emitted_timing_keys()
        assert len(emitted) >= 12, f"유도된 키가 너무 적습니다: {sorted(emitted)}"
        # 개수만 세면 엉뚱한 걸 세도 통과한다. 아는 키가 잡히는지도 본다.
        assert {"rerank_ms", "agent_tool_ms"} <= emitted

    def test_every_pipeline_timing_key_maps_to_a_stage(self):
        """파이프라인이 실제로 쓰는 키가 표에 다 있어야 한다.

        빠지면 그 단계는 조용히 메트릭에서 사라진다 — 하드 필터 값이 빠졌을 때
        아이템이 검색에서 조용히 사라지던 것과 같은 종류의 결함이다.

        **키 목록을 손으로 적지 않는다** (ADR-0052). 예전 판본이 그랬고, 그러면
        새 키를 표에 안 넣었을 때 이 목록에도 안 들어가므로 검사가 조용히
        통과한다. 실제로 셋(`cache_encode_ms`·`cache_lookup_ms`·`domain_gate_ms`)이
        목록 밖에 있었다.
        """
        missing = sorted(emitted_timing_keys() - set(_NOT_A_STAGE) - set(_STAGE_BY_KEY))
        assert not missing, (
            f"파이프라인이 내보내는데 `_STAGE_BY_KEY` 에 없는 키: {missing} — "
            "표에 한 줄 넣거나, 단계가 아니면 `_NOT_A_STAGE` 에 사유와 함께 적으세요."
        )

    def test_the_exclusion_list_is_live(self):
        """**제외를 조용히 두지 않는다** (ADR-0051).

        `_NOT_A_STAGE` 에 적힌 키가 소스에서 사라지면 그 제외는 낡은 채로 남고,
        낡은 제외는 다음에 같은 이름이 생겼을 때 조용히 통과시킨다.
        """
        emitted = emitted_timing_keys()
        stale = sorted(k for k in _NOT_A_STAGE if k not in emitted)
        assert not stale, f"소스에 없는 키가 제외 목록에 남아 있습니다: {stale}"
        for key, reason in _NOT_A_STAGE.items():
            assert len(reason) > 20, f"{key} 의 제외 사유가 비어 있습니다"

    def test_the_table_has_no_dead_rows(self):
        """**반대 방향.** 아무도 안 내보내는 stage 는 영원히 관측 0이다.

        키 이름을 바꾸면 표에는 옛 이름이 남고 새 이름은 안 잡힌다 — 위 검사가
        새 이름을 잡고, 이 검사가 옛 이름을 잡는다.
        """
        dead = sorted(set(_STAGE_BY_KEY) - emitted_timing_keys())
        assert not dead, f"표에 있는데 아무 파이프라인도 안 내보내는 키: {dead}"

    def test_the_derivation_can_actually_fail(self):
        """**공허 방지 — 본 검사와 같은 식(`_keys_in_source`)을 실패 방향으로.**"""
        planted = _keys_in_source('timings = {"brand_new_stage_ms": 1.0}')
        assert planted == {"brand_new_stage_ms"}
        assert planted - set(_NOT_A_STAGE) - set(_STAGE_BY_KEY) == {"brand_new_stage_ms"}

    def test_excluding_the_definition_file_is_what_makes_the_dead_row_check_work(self):
        """**순환 방지 — 무엇을 지키는지까지 좁혀서 적는다.**

        제외가 지키는 것은 위의 **죽은 행 검사**다. `metrics.py` 를 표본에 넣으면
        표의 모든 키가 자동으로 "소스에 있다"가 되므로
        `set(_STAGE_BY_KEY) - emitted` 가 **구조적으로 늘 공집합**이 된다 —
        아래 단언이 정확히 그 사실이다.

        반대 방향(등록을 잊은 새 키)은 제외가 없어도 잡힌다. **제외가 두 방향을
        다 지킨다고 적으면 그건 확인 안 한 근거**이고, 그게 ADR-0051 이 고친
        결함이다.
        """
        assert _DEFINITION.exists(), "제외 대상 경로가 틀렸습니다"
        assert set(_STAGE_BY_KEY) - emitted_timing_keys(skip=None) == set(), (
            "제외를 끄면 죽은 행 검사가 구조적으로 공허해진다 — 그게 제외의 이유다"
        )

    def test_unknown_key_is_dropped_not_guessed(self):
        """`_ms`를 떼는 규칙이 아니라 명시적 표를 쓴다 — 새 키가 조용히 통과하면 안 된다."""
        assert stage_for("something_new_ms") is None

    def test_llm_stages_are_distinguishable(self):
        """검색 분기는 LLM을 2회 부른다. 둘이 한 stage로 뭉치면 분해가 무의미하다."""
        assert stage_for("query_understanding_ms") != stage_for("explain_ms")


class TestOutcome:
    def test_no_results_is_not_an_error_but_is_counted_apart(self):
        assert _outcome({"no_results": True}) == "no_results"

    def test_tool_failure_is_counted_apart(self):
        assert _outcome({"tool_failures": 2}) == "tool_failure"

    def test_plain_response_is_ok(self):
        assert _outcome({"answer": "…"}) == "ok"


class TestCacheResult:
    def test_hit_kind_is_kept(self):
        """정확 일치와 유사도 적중은 성격이 달라서(ADR-0012) 합치면 안 된다."""
        assert cache_result({"hit": True, "match_type": "exact"}) == "hit_exact"
        assert cache_result({"hit": True, "match_type": "semantic"}) == "hit_semantic"

    def test_miss(self):
        assert cache_result({"hit": False}) == "miss"


class TestRecordResponse:
    def test_records_every_known_stage(self):
        before = counts_by_stage()
        record_response(
            "nexon",
            {
                "intent": "item_search",
                "llm_calls": 2,
                "cache": {"hit": False},
                "timings": {
                    "query_understanding_ms": 900.0,
                    "explain_ms": 1050.0,
                    "unknown_ms": 1.0,
                },
            },
        )
        after = counts_by_stage()
        assert after["query_understanding"] == before.get("query_understanding", 0) + 1
        assert after["explain"] == before.get("explain", 0) + 1
        assert "unknown" not in after

    def test_milliseconds_are_converted_to_seconds(self):
        """계기는 초 단위인데 파이프라인은 밀리초로 잰다. 1000배 틀리면
        p95가 그럴듯한 값으로 나와서 눈으로 안 걸린다."""
        record_response(
            "scale-check",
            {"intent": "faq_smalltalk", "llm_calls": 0, "cache": {"hit": False},
             "timings": {"routing_ms": 2000.0}},
        )
        total = next(
            float(line.rsplit(" ", 1)[1])
            for line in render().decode().splitlines()
            if line.startswith("ai_stage_duration_seconds_sum")
            and 'tenant="scale-check"' in line
        )
        assert total == 2.0


class TestUnknownKeyIsNotSilent:
    """모르는 키를 버리되 **한 번은 알린다** (ADR-0053).

    위 `TestStageMapping` 이 소스의 문자열 상수를 훑지만 깊이가 거기까지다 —
    키를 변수나 f-string 으로 만들면 못 본다. 런타임 그물이 그 구멍을 막는다.
    """

    def test_unknown_key_warns_once(self, caplog):
        from app.core import metrics

        metrics._WARNED_UNKNOWN_KEYS.discard("made_up_stage_ms")
        with caplog.at_level("WARNING", logger="app.core.metrics"):
            metrics.record_timings("nexon", {"made_up_stage_ms": 12.0})
            metrics.record_timings("nexon", {"made_up_stage_ms": 12.0})
        hits = [r for r in caplog.records if "made_up_stage_ms" in r.getMessage()]
        assert len(hits) == 1, f"요청마다 경고를 내면 로그가 막힌다: {len(hits)}건"
        metrics._WARNED_UNKNOWN_KEYS.discard("made_up_stage_ms")

    def test_known_key_does_not_warn(self, caplog):
        """**반대 방향.** 정상 키에 경고를 내면 로그가 쓸모없어진다."""
        from app.core import metrics

        with caplog.at_level("WARNING", logger="app.core.metrics"):
            metrics.record_timings("nexon", {"rerank_ms": 5.0})
        assert not [r for r in caplog.records if "rerank_ms" in r.getMessage()]
