"""룰 기반 1차 분기.

계획서의 "룰 우선, 애매하면 KoELECTRA" 중 앞단이다. 대부분의 질의는 표현이
뻔해서 정규식으로 잡히고, 그러면 모델 추론 비용이 0이 된다.

## 설계 원칙: 확신할 때만 답한다

룰은 **명확할 때만 클래스를 반환하고, 조금이라도 애매하면 기권한다.**
잘못 확신하는 룰은 분류기가 고칠 기회조차 뺏는다.

- 정확히 한 의도만 매칭 → 그 의도
- 두 개 이상 매칭 → COMPOUND (도구 하나로는 부족하다는 신호)
- 하나도 매칭 안 됨 → None (분류기로 넘김)

## 가격 "조건"과 가격 "문의"를 구분한다

`"3만원 이하 불속성 검"`의 3만원은 **검색 필터 조건**이고,
`"불속성 검 시세 얼마야"`의 얼마는 **시세 문의**다. 표면에 둘 다 돈 이야기가
나오지만 타야 할 파이프라인이 다르다. 그래서 가격 규칙은 금액 표현이 아니라
**문의 어법**(시세/적정가/오를까/전망)에만 반응하게 짰다.
"""

from __future__ import annotations

import re

from app.services.router.intents import Intent

# 각 의도의 신호 패턴. 재현율보다 정밀도를 우선한다 — 놓친 건 분류기가 잡지만
# 잘못 확신한 건 아무도 못 고친다.
_PATTERNS: dict[Intent, list[str]] = {
    Intent.FAQ_SMALLTALK: [
        r"안녕|반가|고마워|감사|잘 ?가|바이바이",
        r"뭐 ?하는|누구(야|세요)|어떤 ?서비스|사용법|어떻게 ?(써|쓰나|이용)",
        r"수수료|환불|정책|약관|문의|고객센터",
    ],
    Intent.ITEM_SEARCH: [
        r"찾아|검색|보여( ?줘)?|추천|매물|목록|리스트",
        r"있(어|나|을까)\??$",
        r"뭐가 ?있",
    ],
    Intent.PRICE_FORECAST: [
        r"시세|적정가|시가",
        r"얼마",
        r"(오를|내릴|떨어질|올라갈|내려갈)(까|지|건지)?",
        r"전망|추이|예측",
        r"(사도|팔아도) ?(되|괜찮)",
    ],
    Intent.ANOMALY_CHECK: [
        r"이상 ?거래|이상한 ?거래|수상|의심",
        r"사기(야|인가|일까|당한)",
        r"정상 ?거래(인지|야)",
        r"거래 ?\d+ ?번",
    ],
}

_COMPILED = {
    intent: [re.compile(pattern) for pattern in patterns]
    for intent, patterns in _PATTERNS.items()
}


def match_intents(query: str) -> list[Intent]:
    """질의에 신호가 잡힌 의도들을 반환. 판정은 하지 않는다."""
    text = query.strip()
    return [
        intent
        for intent, patterns in _COMPILED.items()
        if any(pattern.search(text) for pattern in patterns)
    ]


def classify_by_rules(query: str) -> Intent | None:
    """룰만으로 판정. 확신이 없으면 None을 반환해 분류기에 넘긴다."""
    matched = match_intents(query)
    if not matched:
        return None
    if len(matched) == 1:
        return matched[0]

    # FAQ는 서비스 메타 질문(수수료·환불·이용법)이라 아이템 도메인 의도와
    # 실제로 복합될 일이 없다. 반면 어휘는 잘 겹친다 — `"수수료 얼마인가요"`가
    # FAQ와 시세 문의 양쪽에 걸려 복합 질의로 빠지고, 에이전트가 "수수료를
    # 조회할 도구가 없다"고 답하는 걸 실제로 관측했다. FAQ 신호가 있으면
    # FAQ로 확정한다.
    if Intent.FAQ_SMALLTALK in matched:
        return Intent.FAQ_SMALLTALK

    # 나머지가 여러 개 잡혔다 = 도구 하나로 못 푼다.
    return Intent.COMPOUND
