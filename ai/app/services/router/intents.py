"""의도 클래스 정의.

기획서의 라우팅 분기와 1:1로 맞춘다. 클래스를 늘리기 전에 반드시 그 분기가
계획서에 있는지 확인할 것 — 분기가 늘면 라우터 정확도 측정도, 학습 데이터도
같이 늘어난다.
"""

from enum import Enum


class Intent(str, Enum):
    FAQ_SMALLTALK = "faq_smalltalk"
    ITEM_SEARCH = "item_search"
    PRICE_FORECAST = "price_forecast"
    ANOMALY_CHECK = "anomaly_check"
    COMPOUND = "compound"
    # 룰도 분류기도 확신하지 못한 경우. 실행 시에는 COMPOUND와 같은 경로를
    # 타지만, 라우팅 품질을 측정할 때 둘을 구분할 수 있어야 해서 따로 둔다.
    UNKNOWN = "unknown"


# 분류기가 학습하는 클래스. COMPOUND는 "도구 여러 개가 필요하다"는 뜻이라
# 발화만으로 라벨링이 가능하지만, UNKNOWN은 모델의 확신도에서 파생되는
# 결과라 학습 대상이 아니다.
TRAINABLE_INTENTS = [
    Intent.FAQ_SMALLTALK,
    Intent.ITEM_SEARCH,
    Intent.PRICE_FORECAST,
    Intent.ANOMALY_CHECK,
    Intent.COMPOUND,
]

INTENT_LABELS = {
    Intent.FAQ_SMALLTALK: "FAQ/스몰토크",
    Intent.ITEM_SEARCH: "아이템 검색",
    Intent.PRICE_FORECAST: "시세 문의",
    Intent.ANOMALY_CHECK: "이상거래 확인",
    Intent.COMPOUND: "복합 질의",
    Intent.UNKNOWN: "판별 불가",
}
