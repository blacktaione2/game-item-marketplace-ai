"""검색 필터 → Elasticsearch filter 절 변환 테스트.

`subcategory` 하드 필터가 이 프로젝트에서 갖는 의미는 **임계값이 없다**는
것이다. 리랭커 점수 하한은 LLM 질의 재작성 노이즈(최대 1.31점) 때문에
캘리브레이션이 불가능했는데(ADR-0014), keyword term 필터는 실행마다
흔들리지 않는다. 그래서 이 변환이 정확한지는 단위 테스트로 고정할 값어치가
있다.
"""

from app.services.search.filters import SearchFilters


def clause_fields(filters: SearchFilters) -> list[str]:
    """생성된 절이 어느 필드를 대상으로 하는지."""
    fields = []
    for clause in filters.to_es_filters():
        for body in clause.values():
            fields.extend(body.keys())
    return fields


class TestSubcategory:
    def test_produces_a_term_clause(self):
        clauses = SearchFilters(subcategory="검").to_es_filters()
        assert clauses == [{"term": {"subcategory": "검"}}]

    def test_absent_when_none(self):
        """종류를 특정할 수 없는 질의에서 필터가 걸리면 맞는 결과까지 사라진다."""
        assert "subcategory" not in clause_fields(SearchFilters(category="무기"))

    def test_combines_with_category_and_price(self):
        fields = clause_fields(
            SearchFilters(category="무기", subcategory="활", price_max=50000)
        )
        assert fields == ["category", "subcategory", "price"]


class TestElement:
    def test_produces_a_term_clause(self):
        clauses = SearchFilters(element="화염").to_es_filters()
        assert clauses == [{"term": {"element": "화염"}}]

    def test_none_and_무속성_are_different(self):
        """`None`은 "속성 조건 없음", `"무속성"`은 "속성 없는 것만".

        둘을 섞으면 `"검 찾아줘"`에서 불속성 검이 사라진다. 값 하나가 두 뜻을
        갖는 필드라 단위 테스트로 못박을 값어치가 있다.
        """
        assert SearchFilters(element=None).to_es_filters() == []
        assert SearchFilters(element="무속성").to_es_filters() == [
            {"term": {"element": "무속성"}}
        ]

    def test_stacks_with_subcategory_and_price(self):
        """`"3만원 이하 불속성 검"` — 이 조합이 코퍼스에서 0건을 만든다."""
        fields = clause_fields(
            SearchFilters(subcategory="검", element="화염", price_max=30000)
        )
        assert fields == ["subcategory", "element", "price"]


class TestRanges:
    def test_price_range_uses_both_bounds(self):
        clauses = SearchFilters(price_min=1000, price_max=5000).to_es_filters()
        assert clauses == [{"range": {"price": {"gte": 1000, "lte": 5000}}}]

    def test_enhancement_and_level_are_separate_axes(self):
        """+9와 100렙은 다른 축이다. 섞이면 엉뚱한 필터가 걸린다."""
        fields = clause_fields(SearchFilters(enhancement_min=9, level_min=100))
        assert fields == ["enhancement_level", "required_level"]

    def test_no_clauses_when_empty(self):
        assert SearchFilters().to_es_filters() == []
