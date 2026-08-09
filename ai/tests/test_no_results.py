"""검색 0건 응답 — LLM 없이 만들어지는 확정 응답.

이 응답은 LLM을 거치지 않으므로 **결정적**이고, 그래서 단위 테스트로 고정할
값어치가 있다. 이전 동작(빈 결과를 LLM에 넘겨 알아서 답하게 하기)은 답이
맞아도 재현이 보장되지 않았다 — ADR-0016.
"""

from app.services.assistant.pipeline import (
    _describe_filters,
    _no_results,
    _search_cost,
    _search_answer,
)


class TestNoResultPayload:
    def test_says_there_is_nothing_and_names_the_conditions(self):
        payload = _no_results(
            {"category": "무기", "subcategory": "검", "element": "화염", "price_max": 30000.0}
        )
        assert payload["no_results"] is True
        assert payload["results"] == []
        assert "검 · 화염 속성 · 30,000원 이하" in payload["answer"]
        assert "없습니다" in payload["answer"]

    def test_costs_llm_calls_not_zero(self):
        """`understand_query`는 건너뛸 수 없다 — 필터를 알아야 0건 판정이 선다.

        값은 세 번 바뀌었다: 2(설명 LLM 있던 시절) → 1(ADR-0036) →
        **2(ADR-0039, 도메인 판정이 병렬로 하나 더 나간다)**.

        바뀌지 않은 건 **검색 분기 전체가 같은 값**이라는 점이다. 그래서
        `llm_calls`로는 0건 여부를 알 수 없고 `no_results`를 봐야 한다.

        **값을 만드는 자리가 옮겨졌다.** 예전에는 `_no_results` 가 상수 2를 적어
        뒀는데, 프로바이더가 죽으면 두 호출이 성사되지 않으므로 그 상수가 거짓이
        된다. 지금은 `_search_cost()` 가 `run_search` 의 결과에서 옮긴다 — 그래서
        여기서도 그쪽을 단언한다.
        """
        assert _search_cost(_search_result(llm_calls=2))["llm_calls"] == 2
        assert "llm_calls" not in _no_results({"subcategory": "검"}), (
            "0건 헬퍼가 다시 상수를 들고 있다 — 같은 사실의 출처가 둘이 된다"
        )

    def test_exposes_the_filters_it_searched_with(self):
        """0건에는 검증할 결과가 없어서 필터가 유일한 근거다.

        재작성이 비결정적인 동안에는 오추출과 진짜 부재를 이걸로만 가릴 수 있다.
        """
        filters = {"subcategory": "검", "element": "화염"}
        payload = _no_results(filters)
        assert payload["applied_filters"] == filters
        assert payload["conditions"] == ["검", "화염 속성"]

    def test_falls_back_when_no_filter_was_extracted(self):
        """필터 없이 0건이면 완화할 조건이 없으므로 다른 안내를 한다."""
        payload = _no_results({})
        assert payload["conditions"] == []
        assert "다른 표현으로" in payload["answer"]


class TestSearchAnswer:
    """결과가 있는 경로도 확정 문장이다 (ADR-0036).

    설명 LLM 을 없앤 계기가 **환각**이라서, 여기서 고정할 것은 문장의 아름다움이
    아니라 **결과와 모순되지 않는가**다. 예전 판본은 22,000원짜리 검 4건을
    받아놓고 "10만원 이하의 검은 없습니다"라고 답했다.
    """

    def test_names_the_conditions_and_the_count(self):
        answer = _search_answer(
            {"category": "무기", "subcategory": "검", "price_max": 100000.0}, 4
        )
        assert "검 · 100,000원 이하" in answer
        assert "4건" in answer

    def test_never_denies_results_that_exist(self):
        """결과가 있는데 '없습니다'라고 말하는 일이 구조적으로 불가능해야 한다.

        이게 이 라운드의 결함 그 자체다 — 프롬프트로는 보장할 수 없어서
        문장 생성을 코드로 옮겼다.
        """
        answer = _search_answer({"subcategory": "검", "price_max": 100000.0}, 4)
        assert "없습니다" not in answer

    def test_falls_back_when_no_filter_was_extracted(self):
        assert _search_answer({}, 3) == "검색 결과 3건입니다."

    def test_speaks_of_conditions_the_same_way_the_empty_path_does(self):
        """두 경로가 같은 `_describe_filters`를 쓴다.

        조건을 다르게 부르면 사용자는 **같은 검색이 상황에 따라 다른 말을
        한다**고 읽는다. 한쪽만 고치는 드리프트를 여기서 막는다.
        """
        filters = {"subcategory": "검", "element": "화염", "price_max": 30000.0}
        conditions = " · ".join(_describe_filters(filters))
        assert conditions in _search_answer(filters, 2)
        assert conditions in _no_results(filters)["answer"]


class TestDescribeFilters:
    def test_subcategory_absorbs_category(self):
        """`검`이 이미 `무기`를 함의한다 — 둘 다 쓰면 군더더기다."""
        assert _describe_filters({"category": "무기", "subcategory": "검"}) == ["검"]

    def test_category_alone_survives(self):
        assert _describe_filters({"category": "무기"}) == ["무기"]

    def test_무속성_is_not_given_a_속성_suffix(self):
        assert _describe_filters({"element": "무속성"}) == ["무속성"]
        assert _describe_filters({"element": "냉기"}) == ["냉기 속성"]

    def test_enhancement_and_level_stay_distinct(self):
        """+9와 100렙은 다른 축이다 — 문구에서도 섞이면 안 된다."""
        assert _describe_filters({"enhancement_min": 9, "level_min": 100}) == [
            "+9 이상",
            "100렙 이상",
        ]

    def test_price_is_formatted_with_thousands_separators(self):
        assert _describe_filters({"price_max": 30000.0}) == ["30,000원 이하"]

    def test_sale_type_is_translated(self):
        assert _describe_filters({"sale_type": "AUCTION"}) == ["경매"]


def _search_result(llm_calls: int, degraded: bool = False) -> dict:
    """`run_search` 가 돌려주는 것 중 `_search_cost` 가 쓰는 만큼만."""
    return {"llm_calls": llm_calls, "timings": {}, "degraded": degraded}
