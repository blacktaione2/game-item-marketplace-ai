"""`timings` dict → Prometheus 히스토그램 변환.

계측 지점이 `record_response` 하나뿐이라 여기만 고정하면 파이프라인 전체가
덮인다. 반대로 이 변환이 조용히 틀리면 **부하테스트가 엉뚱한 곳을 지목한다** —
숫자가 나오긴 하므로 눈으로는 안 걸린다.
"""

from app.core.metrics import (
    _STAGE_BY_KEY,
    _outcome,
    cache_result,
    record_response,
    render,
    stage_for,
)


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
    def test_every_pipeline_timing_key_maps_to_a_stage(self):
        """파이프라인이 실제로 쓰는 키가 표에 다 있어야 한다.

        빠지면 그 단계는 조용히 메트릭에서 사라진다 — 하드 필터 값이 빠졌을 때
        아이템이 검색에서 조용히 사라지던 것과 같은 종류의 결함이다.
        """
        emitted = {
            # assistant/pipeline.py
            "cache_ms", "routing_ms", "execution_ms", "explain_ms",
            # search/pipeline.py
            "query_understanding_ms", "embedding_ms", "retrieval_ms", "rerank_ms",
            # forecast/pipeline.py
            "window_ms", "inference_ms",
            # anomaly/pipeline.py
            "scoring_ms",
            # agent/agent.py
            "agent_llm_ms", "agent_tool_ms",
        }
        assert emitted <= set(_STAGE_BY_KEY)

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
