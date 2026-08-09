"""Text-to-DSL이 뽑아낸 구조화 필터 → Elasticsearch filter 절 변환."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SearchFilters(BaseModel):
    """LLM이 자연어에서 추출하는 구조화 필터. 전부 optional."""

    category: str | None = None
    # 세부 종류. category(`무기`)로는 검/활/지팡이가 구분되지 않아 텍스트
    # 신호에만 의존했고, 그 결과 `"5만원 이하 검"`에 활이 섞였다.
    subcategory: str | None = None
    # 속성. `"불속성 검"`은 subcategory까지 걸어도 검류 전체를 돌려줬다 —
    # 속성이 텍스트 신호로만 남아 있었기 때문이다.
    # `None`은 "속성 조건 없음", `"무속성"`은 "속성이 없는 아이템만"이다.
    element: str | None = None
    sale_type: str | None = None
    price_min: float | None = None
    price_max: float | None = None
    enhancement_min: int | None = None
    enhancement_max: int | None = None
    # 착용 요구 레벨. "100렙 이상"은 level_min=100 (그 레벨대 장비를 찾는다는
    # 뜻이지, 그 레벨로 낄 수 있는 하위 장비를 달라는 뜻이 아니다).
    level_min: int | None = None
    level_max: int | None = None

    def to_es_filters(self) -> list[dict[str, Any]]:
        clauses: list[dict[str, Any]] = []

        if self.category:
            clauses.append({"term": {"category": self.category}})
        if self.subcategory:
            clauses.append({"term": {"subcategory": self.subcategory}})
        if self.element:
            clauses.append({"term": {"element": self.element}})
        if self.sale_type:
            clauses.append({"term": {"sale_type": self.sale_type}})

        price_range = _range(self.price_min, self.price_max)
        if price_range:
            clauses.append({"range": {"price": price_range}})

        enhancement_range = _range(self.enhancement_min, self.enhancement_max)
        if enhancement_range:
            clauses.append({"range": {"enhancement_level": enhancement_range}})

        level_range = _range(self.level_min, self.level_max)
        if level_range:
            clauses.append({"range": {"required_level": level_range}})

        return clauses


class QueryUnderstanding(BaseModel):
    """Query Rewrite + Text-to-DSL의 단일 LLM 호출 결과."""

    rewritten_query: str
    filters: SearchFilters = Field(default_factory=SearchFilters)
    # **도메인 판정(`in_domain`)은 여기 없다.** 한 번 넣어봤고 측정으로
    # 기각됐다(ADR-0039). 이 프롬프트에 필드를 하나 더하면 추출이 같이 흔들린다 —
    # 재작성 토큰집합 일치도가 대조군 대비 **-0.24** 떨어졌고, 문구를 고쳐도
    # 그대로였다(-0.234 → -0.248). 스키마가 늘어난 것 자체의 대가다.
    # 판정은 `domain_gate.judge_in_domain()` 이 **별도 호출로 병렬** 수행한다.

    # 이 값이 LLM 이 아니라 **폴백에서 나왔는가** (ADR-0041 의 뒤늦은 짝).
    #
    # 프롬프트에 나가는 값이 아니라 **호출 결과에 붙는 꼬리표**라, 위 문단이
    # 기각한 `in_domain` 과 성격이 다르다 — 스키마가 늘지 않는다.
    #
    # 필요한 이유: 시세·이상거래 분기는 설명 LLM 이 죽으면 `degraded` 를 세우는데
    # 검색 분기만 못 세웠다. 검색은 LLM 이 다 죽어도 **필터 없는 검색**으로 답을
    # 내므로 500 도 안 나고 응답도 그럴듯하다 — 즉 신호가 아예 없었다.
    degraded: bool = False


def _range(minimum: float | int | None, maximum: float | int | None) -> dict[str, Any]:
    bounds: dict[str, Any] = {}
    if minimum is not None:
        bounds["gte"] = minimum
    if maximum is not None:
        bounds["lte"] = maximum
    return bounds
