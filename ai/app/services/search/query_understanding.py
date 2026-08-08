"""Query Rewrite + Text-to-DSL을 LLM 호출 "1회"로 처리한다.

동의어 확장(Query Rewrite)과 구조화 필터 추출(Text-to-DSL)을 각각 호출하면
검색 한 번에 LLM 왕복이 2회 붙는다. 계획서의 레이턴시/비용 기조에 맞춰
하나의 프롬프트에서 JSON으로 둘 다 받아낸다.

Phase 2에서 만든 LLMClient 인터페이스를 그대로 재사용하므로, 프로바이더가
바뀌어도 이 모듈은 손댈 필요가 없다.
"""

from __future__ import annotations

import json
import logging
import re

from app.services.llm.base import LLMClient
from app.services.search.filters import QueryUnderstanding, SearchFilters

logger = logging.getLogger(__name__)

# 질의에 글자 그대로 있으면 `element="무속성"` 이 확실한 표현들 (ADR-0040).
#
# **`무속성` 하나만 다룬다.** 다른 속성으로 넓히지 않는 이유가 있다 — `"화염 저항
# 방어구"` 는 `화염` 을 담고 있지만 정답이 `null` 이고, 지금 프롬프트가 그걸 맞게
# 처리한다(정확도 97.5%). 낱말로 밀면 **지금 되는 것을 깨뜨린다.** 무속성에는
# 그런 짝(무속성 저항)이 없다.
_NO_ELEMENT = re.compile(r"무속성|속성\s*없")

# 부정형이면 채우지 않는다. `"무속성 아닌 검"` 에 `무속성` 을 채우면 사용자가
# 원한 것의 **정반대**를 준다. `SearchFilters.element` 는 동등 비교 하나뿐이라
# "무속성 제외" 를 표현할 수 없으므로, 할 수 있는 최선은 **필터를 안 거는 것**이다.
#
# 한국어의 구분은 어미에 있는데 정규식은 명사에 걸기 쉽다 — 이 저장소가 정규식에서
# 두 번, 프롬프트에서 두 번 겪은 실패다. 그래서 부정을 먼저 본다.
#
# **괄호가 없으면 `|` 가 전체를 가른다.** 처음 쓴 판본이
# `무속성|속성\s*없...아닌` 이었는데, 그러면 `무속성` 하나만으로 매칭돼서
# **모든 질의가 부정형으로 판정되고 후처리가 한 번도 안 걸린다.** 증상은 "고쳤는데
# 아무것도 안 변했다" 이고, 아래 테스트가 그걸 잡는다.
_NEGATED = re.compile(
    r"(?:무속성|속성\s*없\S*)\S*\s*(?:거|것|건)?\s*(?:아닌|아니|말고|빼고|제외|외에)"
)


def fill_missing_element(query: str, element: str | None) -> str | None:
    """추출이 놓친 `무속성` 을 질의 원문에서 채운다 (ADR-0040).

    ## 왜 프롬프트가 아니라 코드인가

    프롬프트에는 이미 `무속성/속성 없는 -> "무속성"` 과 `null` 과의 차이가 적혀
    있다. **정확히 옳은 말로 이미 지시받았는데도 50번 중 42번을 틀린다** — 모델이
    "무속성" 을 "속성 언급 없음" 으로 읽는다. 그리고 이 자리는 프롬프트 한 줄로
    97.5% → 22% 를 겪은 자리다(혼동 대상을 이름으로 부르면 그쪽으로 쏠렸다).

    **결정 단계가 보장할 수 있는 것을 LLM 에게 다시 묻지 않는다** — ADR-0036 이
    검색 설명 LLM 을 없애고 ADR-0039 가 `_out_of_domain()` 에서 인자를 없앤 것과
    같은 처방이다. 질의에 글자가 있는지는 코드가 확실히 안다.

    ## 채우기만 하고 덮어쓰지 않는다

    `element` 가 이미 있으면 손대지 않는다. 그래서 이 함수는

    - 지금 잘 되는 다른 속성 추출(97.5%)에 **닿을 수 없고**
    - `"불속성"` 이 `무속성` 으로 잘못 나오는 별개 결함(약 1%)도 건드리지 않는다
      — 그건 `null` 이 아니라 잘못된 값이라 여기 조건에 안 걸린다

    실패 방향도 한쪽이다: 질의에 표현이 없으면 아무 일도 안 일어난다. `"검 찾아줘"`
    에 무속성이 끼는 사고는 **구조적으로 불가능**하다.
    """
    negated = _NEGATED.search(query) is not None

    if element is not None:
        # **채우기만 하던 함수가 지우기도 하게 됐다** (ADR-0045). 모델을
        # `gpt-5.4-mini` 로 올리니 `무속성` 미채움은 사라졌는데(69% -> 100%)
        # 대신 **부정형에 스스로 `무속성` 을 채우는** 새 결함이 생겼다 —
        # `"무속성 빼고 보여줘"` 에 `element="무속성"`. 채우기만 해서는 그걸
        # 못 막는다.
        #
        # **`무속성` 일 때만 지운다.** 다른 속성은 손대지 않으므로 아래 문단의
        # "지금 잘 되는 것에 닿을 수 없다" 는 성질이 유지된다.
        if element == "무속성" and negated:
            logger.debug("무속성 부정형인데 채워져 있다 - 필터를 지운다: %s", query)
            return None
        return element

    if not _NO_ELEMENT.search(query):
        return None
    if negated:
        # 부정형은 필터 없음으로 남긴다 — 표현할 수 없는 것을 반대로 표현하느니
        # 안 거는 게 낫다. 알려진 한계이고 평가셋이 별도 무리로 집계한다.
        logger.debug("무속성 부정형 - 필터를 채우지 않는다: %s", query)
        return None
    return "무속성"

_PROMPT = """당신은 게임 아이템 거래소의 검색 질의를 분석하는 도우미입니다.
사용자의 자연어 검색어를 분석해서 아래 JSON 스키마로만 응답하세요.
설명이나 코드블록 없이 JSON 객체 하나만 출력합니다.

{{
  "rewritten_query": "검색에 쓸 재작성된 질의 (동의어/약어를 펼치고, 가격·강화수치 같은 조건 표현은 제거)",
  "filters": {{
    "category": "무기|방어구|장신구|소모품|계정|재화 중 하나 또는 null",
    "subcategory": "검|둔기|창|활|지팡이|갑옷|방패|신발|로브|모자|각반|반지|목걸이|팔찌|귀걸이|물약|주문서|재료|계정|재화 중 하나 또는 null",
    "element": "화염|냉기|번개|암흑|신성|무속성 중 하나 또는 null",
    "sale_type": "FIXED_PRICE|AUCTION 중 하나 또는 null",
    "price_min": 숫자 또는 null,
    "price_max": 숫자 또는 null,
    "enhancement_min": 숫자 또는 null,
    "enhancement_max": 숫자 또는 null,
    "level_min": 숫자 또는 null,
    "level_max": 숫자 또는 null
  }}
}}

규칙:
- 조건에 해당하지 않는 필드는 반드시 null로 두세요. 추측하지 마세요.
- **아이템 종류가 명시되면 subcategory를 반드시 채우세요.** 이게 비면 종류가
  다른 아이템이 섞여 나옵니다. 표기 대응:
  - 검/소드/롱소드/대검/단검/암살검 -> "검"
  - 해머/둔기/워메이스/망치 -> "둔기"
  - 창/랜스 -> "창"
  - 활/궁/장궁/단궁/쇠뇌/보우 -> "활"
  - 지팡이/스태프/마법봉/마법구/완드 -> "지팡이"
  - 갑옷/사슬갑/판금/경갑 -> "갑옷"
  - 방패/실드 -> "방패", 장화/신발/부츠 -> "신발", 로브 -> "로브"
  - 후드/모자/투구 -> "모자", 각반 -> "각반"
  - 반지 -> "반지", 목걸이 -> "목걸이", 팔찌 -> "팔찌", 귀걸이 -> "귀걸이"
  - 물약/포션 -> "물약", 주문서/스크롤 -> "주문서", 재료/파편 -> "재료"
  - 계정 -> "계정", 골드/머니/재화 -> "재화"
- 종류를 특정할 수 없는 질의("싼 아이템", "강한 무기")는 subcategory를 null로
  두세요. 억지로 채우면 맞는 결과까지 걸러집니다.
- **속성이 명시되면 element를 반드시 채우세요.** 표기 대응:
  - 불/불속성/화염/화염속성/파이어 -> "화염"
  - 얼음/얼음속성/냉기/서리/아이스 -> "냉기"
  - 번개/전기/뇌전/라이트닝 -> "번개"
  - 어둠/어둠속성/암흑/다크 -> "암흑"
  - 빛/성속성/신성/홀리 -> "신성"
  - 무속성/속성 없는 -> "무속성"
- **속성이 언급되지 않은 질의는 element를 null로 두세요.** null("속성 조건
  없음")과 "무속성"("속성이 없는 아이템만")은 다릅니다. `"검 찾아줘"`에
  "무속성"을 넣으면 불속성 검이 사라집니다.
- 속성 **저항**을 찾는 질의("화염 저항 방어구")는 element를 null로 두세요.
  element는 그 아이템이 내는 속성이고, 저항은 별개 축입니다.
- "3만원 이하" 같은 표현은 price_max: 30000 으로 변환하세요.
- "+9 이상"은 enhancement_min: 9 입니다. (강화 수치)
- "100렙 이상", "100레벨 이상"은 level_min: 100 입니다. (착용 요구 레벨)
  강화 수치(+숫자)와 레벨은 서로 다른 축이니 절대 섞지 마세요.
- rewritten_query에는 필터로 뽑아낸 조건을 남기지 마세요.
- **rewritten_query에는 반드시 동의어를 펼쳐서 함께 넣으세요.** 키워드 검색이
  걸리려면 아이템명에 실제로 쓰일 법한 표현이 있어야 합니다. 특히:
  - 속성 표현: "불속성" -> "불꽃 화염", "얼음속성" -> "얼음 냉기 서리",
    "어둠속성" -> "암흑 어둠"
  - 장비 종류: "검" -> "검 소드 대검 단검", "지팡이" -> "지팡이 마법봉",
    "갑옷" -> "갑옷 방어구 로브"
  - 약어: "렙" -> "레벨", "공깁" -> "공격력"

예시:
입력: "3만원 이하 불속성 검"
출력: {{"rewritten_query": "불꽃 화염 검 소드", "filters": {{"category": "무기", "subcategory": "검", "element": "화염", "sale_type": null, "price_min": null, "price_max": 30000, "enhancement_min": null, "enhancement_max": null, "level_min": null, "level_max": null}}}}
입력: "100렙 이상 활"
출력: {{"rewritten_query": "활 장궁 단궁", "filters": {{"category": "무기", "subcategory": "활", "element": null, "sale_type": null, "price_min": null, "price_max": null, "enhancement_min": null, "enhancement_max": null, "level_min": 100, "level_max": null}}}}
입력: "싼 아이템 추천"
출력: {{"rewritten_query": "저렴한 아이템", "filters": {{"category": null, "subcategory": null, "element": null, "sale_type": null, "price_min": null, "price_max": null, "enhancement_min": null, "enhancement_max": null, "level_min": null, "level_max": null}}}}

사용자 검색어: {query}"""


async def understand_query(llm_client: LLMClient, query: str) -> QueryUnderstanding:
    """자연어 질의 → (재작성 질의, 구조화 필터).

    LLM 호출이 실패하거나 파싱이 안 되면 원본 질의를 그대로 쓰고 필터는
    비운다 — 검색 자체가 죽는 것보단 필터 없는 검색이 낫다.
    """
    try:
        understanding = _parse(await llm_client.complete(_PROMPT.format(query=query)))
    except Exception:
        logger.warning("질의 이해 실패, 원본 질의로 폴백합니다.", exc_info=True)
        understanding = QueryUnderstanding(
            rewritten_query=query, filters=SearchFilters()
        )

    # **폴백 경로에도 적용한다.** 규칙이 "질의에 무속성이 있고 아무도 안 뽑았으면
    # 채운다" 하나여야 어디서 걸리는지 헷갈리지 않는다. 폴백은 "필터 없는 검색"
    # 이지만, 여기서 채우는 값은 LLM 의 추측이 아니라 **질의 원문에 있는 글자**라
    # 그 취지와 어긋나지 않는다.
    understanding.filters.element = fill_missing_element(
        query, understanding.filters.element
    )
    return understanding


def _parse(raw: str) -> QueryUnderstanding:
    """LLM 응답에서 JSON을 추출해 파싱. 코드블록으로 감싸 오는 경우를 흡수한다."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"JSON 객체를 찾을 수 없습니다: {raw[:200]}")

    return QueryUnderstanding.model_validate(json.loads(text[start : end + 1]))
