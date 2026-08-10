"""검색 필터 → Elasticsearch filter 절 변환 테스트.

`subcategory` 하드 필터가 이 프로젝트에서 갖는 의미는 **임계값이 없다**는
것이다. 리랭커 점수 하한은 LLM 질의 재작성 노이즈(최대 1.31점) 때문에
캘리브레이션이 불가능했는데(ADR-0014), keyword term 필터는 실행마다
흔들리지 않는다. 그래서 이 변환이 정확한지는 단위 테스트로 고정할 값어치가
있다.
"""

import json
from typing import Any, get_args

from app.services.assistant.pipeline import _describe_filters
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


def clauses_per_field(model: type[SearchFilters]) -> dict[str, str]:
    """필드 하나씩만 채웠을 때 나오는 절. 직렬화해서 비교 가능하게 만든다."""
    return {
        name: json.dumps(
            model(**{name: _sample_for(field.annotation)}).to_es_filters(),
            sort_keys=True,
            ensure_ascii=False,
        )
        for name, field in model.model_fields.items()
    }


def unhandled_fields(model: type[SearchFilters]) -> list[str]:
    """값을 넣어도 **절을 하나도 안 만드는** 필드들.

    본 검사와 공허 방지가 이 식을 공유한다.

    **깊이: 존재이지 대응이 아니다.** 절이 하나라도 나오면 통과하므로,
    `rarity` 를 더하면서 `{"term": {"element": self.rarity}}` 라고 **대상만
    잘못 적은** 경우는 이 함수가 못 잡는다. 그쪽은 아래
    `test_no_two_fields_produce_the_same_clause` 가 맡는다 — 잘못 적은 대상은
    원래 그 대상을 쓰는 필드와 **같은 절**을 내므로 충돌로 드러난다.
    """
    empty = json.dumps([], sort_keys=True)
    return [
        name for name, clause in clauses_per_field(model).items() if clause == empty
    ]


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

    def test_no_two_fields_produce_the_same_clause(self):
        """**대상을 잘못 적은 경우를 잡는다** (ADR-0054).

        `unhandled_fields()` 는 *존재*만 본다 — 절이 하나라도 나오면 통과한다.
        그래서 `rarity` 를 더하면서 `{"term": {"element": self.rarity}}` 라고
        **대상만 잘못 적으면** 그 검사는 통과하고, 검색은 엉뚱한 필드로 걸린다.

        실측: 지금 10개 필드가 **전부 다른 절**을 낸다(`price_min`/`price_max` 도
        `gte`/`lte` 로 갈린다). 그래서 충돌은 곧 "같은 대상을 두 번 썼다"는 뜻이다.
        """
        seen: dict[str, list[str]] = {}
        for name, clause in clauses_per_field(SearchFilters).items():
            seen.setdefault(clause, []).append(name)
        collisions = {c: names for c, names in seen.items() if len(names) > 1}
        assert not collisions, (
            f"서로 다른 필드가 같은 절을 냅니다: {list(collisions.values())} — "
            "변환에서 대상 필드를 잘못 적었을 가능성이 큽니다."
        )

    def test_the_collision_check_can_actually_fail(self):
        """**공허 방지 — 대상만 잘못 적은 모양을 그대로 만든다.**"""

        class _RarityWithWrongTarget(SearchFilters):
            rarity: str | None = None

            def to_es_filters(self):
                clauses = super().to_es_filters()
                if self.rarity:
                    # 복사해놓고 대상을 안 바꿨다 — 실제로 잘 나는 실수다.
                    clauses.append({"term": {"element": self.rarity}})
                return clauses

        assert unhandled_fields(_RarityWithWrongTarget) == [], (
            "존재 검사만으로는 통과한다 — 그래서 충돌 검사가 필요하다"
        )
        per_field = clauses_per_field(_RarityWithWrongTarget)
        assert per_field["rarity"] == per_field["element"]

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


class TestDescriptionMatchesWhatIsActuallyFiltered:
    """**세 번째 손 열거** — 그리고 이 하나가 거짓말을 만든다 (ADR-0054).

    필드 하나가 세 곳에 손으로 적혀 있다.

    | # | 어디 | 무엇을 정하나 |
    |---|---|---|
    | 1 | `SearchFilters.model_fields` | LLM 이 무엇을 뽑는가 |
    | 2 | `to_es_filters()` | 무엇으로 **실제로** 거르는가 |
    | 3 | `_describe_filters()` | 사용자에게 무엇으로 걸렀다고 **말하는가** |

    빠지는 방향마다 결과가 다르다.

    - **1에만 있다** → 조용히 안 걸린다(사례 45). `unhandled_fields()` 가 잡는다
    - **2에만 있다** → 걸리는데 설명에 안 나온다. 가볍다
    - **3에만 있다** → **걸리지도 않았는데 걸렀다고 말한다.** 0건 응답이
      *"전설 등급 조건에 맞는 매물이 없습니다"* 라고 하는데 등급은 필터에
      안 들어간 상태다

    세 번째가 심각한 이유는 자리 때문이다. ADR-0016 이 0건 응답에서 설명 LLM 을
    **없앤** 것은 그 문장이 지어내지 않게 하려는 것이었고, 그 자리의 문구가
    `_describe_filters()` 다. 그리고 `applied_filters` 는 **이름 자체가 적용을
    주장**하면서, 주석이 *"0건일 때 사용자가 가진 유일한 근거"* 라고 적어둔다.
    **값이 유일한 근거인 자리가 곧 그 값이 거짓일 수 있는 자리다.**
    """

    def _both(self, model=SearchFilters, describe=_describe_filters) -> dict:
        """필드마다 (절이 나오는가, 문구가 나오는가).

        **모델과 설명 함수를 인자로 받는다** — 공허 방지가 *"설명에만 있는 상태"*
        를 실제로 만들어 **이 함수에 통과시킬** 수 있어야 한다. 첫 판본은 그
        상태를 흉내 낸 지역 함수를 만들고 그 함수만 단언했다. 즉 **본 검사의
        비교식(`filtered != described`)이 실패 방향으로 한 번도 안 돌았다** —
        사례 44 를 적은 바로 다음 라운드에 같은 실수다(사례 48).

        깊이: **필드 하나씩** 본다. `_describe_filters` 는 `subcategory` 가 있으면
        `category` 를 일부러 생략하므로, 여러 필드를 같이 채우면 이 대응은
        의도적으로 깨진다. 그건 결함이 아니라 축약 규칙이다.
        """
        result = {}
        for name, field in model.model_fields.items():
            instance = model(**{name: _sample_for(field.annotation)})
            result[name] = (
                bool(instance.to_es_filters()),
                bool(describe(instance.model_dump(exclude_none=True))),
            )
        return result

    def test_filtered_and_described_agree(self):
        mismatched = {
            name: ("걸리는데 설명 없음" if filtered else "설명하는데 안 걸림")
            for name, (filtered, described) in self._both().items()
            if filtered != described
        }
        assert not mismatched, (
            f"필터와 설명이 어긋납니다: {mismatched} — "
            "'설명하는데 안 걸림' 은 0건 응답이 거짓을 말한다는 뜻입니다."
        )

    def test_there_is_something_to_compare(self):
        """양쪽 다 0이면 위 검사는 공짜로 통과한다."""
        both = self._both()
        assert sum(1 for f, _ in both.values() if f) >= 8
        assert sum(1 for _, d in both.values() if d) >= 8

    def test_the_comparison_can_actually_fail(self):
        """**공허 방지 — 3에만 있는 모양을 그대로 만든다.**

        `rarity` 를 모델과 설명에는 넣고 변환에는 안 넣었다. 이게 실제로 나는
        순서다 — 문구는 답변을 쓰다가 자연히 추가되고, DSL 은 따로 손대야 한다.
        """

        class _RarityDescribedNotFiltered(SearchFilters):
            rarity: str | None = None

        def _describe_with_rarity(dumped):
            parts = _describe_filters(dumped)
            if dumped.get("rarity"):
                parts.append(f"{dumped['rarity']} 등급")
            return parts

        # **본 검사와 같은 식을 돌린다.** 흉내 낸 함수만 단언하면 비교식이
        # 실패 방향으로 한 번도 안 돈다 (사례 44 · 48).
        both = self._both(model=_RarityDescribedNotFiltered, describe=_describe_with_rarity)
        assert both["rarity"] == (False, True), both["rarity"]
        mismatched = [n for n, (f, d) in both.items() if f != d]
        assert mismatched == ["rarity"], (
            f"설명만 있고 필터는 없는 상태를 비교식이 지목해야 한다: {mismatched}"
        )
