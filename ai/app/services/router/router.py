"""2단 라우팅: 룰 우선, 애매하면 분류기, 그래도 애매하면 에이전트.

```
질의 → 룰
        ├ 확정 → 그 의도
        └ 기권 → KoELECTRA
                  ├ confidence >= τ → 그 의도
                  └ confidence <  τ → COMPOUND
```

**애매하면 COMPOUND로 보낸다.** "이 아이템 적정가야?"가 검색인지 시세예측인지
억지로 가리지 않는다 — 애매하다는 것 자체가 "도구 하나로는 부족"이라는 신호다.
오분류(엉뚱한 답)의 비용이 에이전트 경로 비용(느리고 비쌈)보다 크다.

모델이 아직 학습되지 않았으면 룰만으로 동작하고, 룰이 기권하면 COMPOUND로
보낸다 — 분류기가 없다고 라우팅 자체가 죽지는 않는다.
"""

from __future__ import annotations

from typing import Any

from app.core.config import get_settings
from app.services.router.classifier import get_intent_classifier
from app.services.router.intents import Intent
from app.services.router.rules import classify_by_rules


def route(query: str) -> dict[str, Any]:
    """의도 판정 결과와 그 근거를 반환한다.

    근거(`decided_by`, `confidence`)를 같이 돌려주는 건 라우팅 품질을 나중에
    측정하고 튜닝하기 위해서다. 어느 단계가 판정했는지 모르면 임계값을
    조정할 근거가 없다.
    """
    matched = classify_by_rules(query)
    if matched is not None:
        return {"intent": matched, "decided_by": "rules", "confidence": None}

    classifier = get_intent_classifier()
    if not classifier.is_available():
        # 분류기 미학습 — 룰이 기권했으니 에이전트에게 넘긴다.
        return {
            "intent": Intent.COMPOUND,
            "decided_by": "fallback_no_model",
            "confidence": None,
        }

    intent, confidence = classifier.predict(query)
    threshold = get_settings().intent_confidence_threshold
    if confidence < threshold:
        return {
            "intent": Intent.COMPOUND,
            "decided_by": "low_confidence",
            "confidence": round(confidence, 4),
            "classifier_intent": intent,
        }
    return {
        "intent": intent,
        "decided_by": "classifier",
        "confidence": round(confidence, 4),
    }
