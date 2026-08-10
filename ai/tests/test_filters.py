"""검색 필터 → Elasticsearch filter 절 변환 테스트.

`subcategory` 하드 필터가 이 프로젝트에서 갖는 의미는 **임계값이 없다**는
것이다. 리랭커 점수 하한은 LLM 질의 재작성 노이즈(최대 1.31점) 때문에
캘리브레이션이 불가능했는데(ADR-0014), keyword term 필터는 실행마다
흔들리지 않는다. 그래서 이 변환이 정확한지는 단위 테스트로 고정할 값어치가
있다.
"""

from typing import Any, get_args

from app.services.search.filters import SearchFilters


def clause_fields(filters: SearchFilters) -> list[str]:
    """생성된 절이 어느 필드를 대상으로 하는지."""
    fields = []
    for clause in filters.to_es_filters():
        for body in clause.values():
            fields.extend(body.keys())
    return fields


def _sample_for(annotation: Any) -> Any:
    """그 타입의 대표값. **타입에서 유도한다** — 필드별 표본을 손으로 적으면
    그 목록이 곧 다음에 새는 열거다."""
    inner = [a for a in get_args(annotation) if a is not type(None)]
    base = inner[0] if inner else annotation
    if base is str:
        return "표본"
    if base is int:
        return 1
    if base is float:
        return 1.0
    raise AssertionError(f"표본을 만들 수 없는 타입입니다: {annotation}")


def unhandled_fields(model: type[SearchFilters]) -> list[str]:
    """값을 넣어도 **절을 하나도 안 만드는** 필드들.

    본 검사와 공허 방지가 이 식을 공유한다.
    """
    missing = []
    for name, field in model.model_fields.items():
        instance = model(**{name: _sample_for(field.annotation)})
        if not instance.to_es_filters():
            missing.append(name)
    return missing


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


class TestEveryFieldIsActuallyUsed:
    """모델에 필드를 더하고 **변환에 안 넣으면** 그 필터는 조용히 무력해진다.

    ## 왜 조용한가

    `to_es_filters()` 는 필드를 **하나씩 손으로** 옮긴다. 하나를 빠뜨리면

    - LLM 은 그 필드를 뽑는다(스키마에 있으니 프롬프트가 안내한다)
    - 응답의 `filters` 에는 **추출된 값이 그대로 실린다** — 적용된 것처럼 보인다
    - ES 에는 안 걸린다. 결과는 그냥 **필터 없는 검색**이다

    예외도 로그도 없고, 화면은 값을 보여준다. 이 저장소는 같은 계열을 이미
    겪었다 — `HARD_FILTER_FIELDS` 가 빠진 아이템은 **검색에서 조용히 사라졌고**,
    그래서 `corpus/__init__.py` 가 임포트 시점에 단언한다. 이건 그 거울상이다:
    모델에는 있고 변환에는 없는 필드.

    로드맵에 **`rarity` 축**이 열려 있다(등급 분류 체계를 만들어야 하는 건).
    다음에 필드를 더하는 사람이 정확히 이 자리로 온다.
    """

    def test_every_field_produces_at_least_one_clause(self):
        missing = unhandled_fields(SearchFilters)
        assert not missing, (
            f"모델에 있는데 `to_es_filters()` 가 안 쓰는 필드: {missing} — "
            "값이 추출돼도 ES 에는 안 걸립니다. 응답에는 실려서 적용된 것처럼 보입니다."
        )

    def test_there_are_fields_to_check(self):
        """0개를 세면 위 검사는 공짜로 통과한다."""
        assert len(SearchFilters.model_fields) >= 8

    def test_the_check_can_actually_fail(self):
        """**공허 방지 — 같은 식을 실패 방향으로.**

        로드맵에 열려 있는 축(`rarity`)을 그대로 써서, 다음에 실제로 일어날
        모양을 만든다. 지어낸 이름으로 하면 "필드를 더했다"가 아니라 정규식만
        시험하게 된다.
        """

        class _WithRarity(SearchFilters):
            rarity: str | None = None

        assert unhandled_fields(_WithRarity) == ["rarity"]

    def test_a_handled_subclass_is_not_flagged(self):
        """**반대 방향.** 제대로 변환을 더한 필드는 지목하면 안 된다."""

        class _WithRarityHandled(SearchFilters):
            rarity: str | None = None

            def to_es_filters(self):
                clauses = super().to_es_filters()
                if self.rarity:
                    clauses.append({"term": {"rarity": self.rarity}})
                return clauses

        assert unhandled_fields(_WithRarityHandled) == []
