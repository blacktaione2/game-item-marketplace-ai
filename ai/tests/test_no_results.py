"""검색 0건 응답 — LLM 없이 만들어지는 확정 응답.

이 응답은 LLM을 거치지 않으므로 **결정적**이고, 그래서 단위 테스트로 고정할
값어치가 있다. 이전 동작(빈 결과를 LLM에 넘겨 알아서 답하게 하기)은 답이
맞아도 재현이 보장되지 않았다 — ADR-0016.
"""

from app.services.assistant.pipeline import _describe_filters, _no_results


class TestNoResultPayload:
    def test_says_there_is_nothing_and_names_the_conditions(self):
        payload = _no_results(
            {"category": "무기", "subcategory": "검", "element": "화염", "price_max": 30000.0}
        )
        assert payload["no_results"] is True
        assert payload["results"] == []
        assert "검 · 화염 속성 · 30,000원 이하" in payload["answer"]
        assert "없습니다" in payload["answer"]

    def test_costs_one_llm_call_not_zero(self):
        """`understand_query`는 건너뛸 수 없다 — 필터를 알아야 0건 판정이 선다.

        없어지는 건 설명 생성 호출 하나뿐이다(2 → 1). 0회는 캐시 적중 경로다.
        """
        assert _no_results({"subcategory": "검"})["llm_calls"] == 1

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
