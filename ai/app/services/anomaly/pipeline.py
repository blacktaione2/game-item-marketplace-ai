"""이상거래 탐지 오케스트레이션.

거래 →
  (1) 맥락 피처 산출 (시세 대비 가격, 계정 나이, 롤링 행동 지표)
  (2) 오토인코더 재구성 → 이상 점수
  (3) 피처별 기여도 분해
→ 판정 + 근거

LLM 자연어 설명은 여기서 만들지 않는다 — Phase 6 Intent Router가 붙을 때
연결한다.

**주의**: 지금 조회 대상은 Postgres `trades` 테이블이 아니라 `app/corpus`의
더미 타임라인이다(Phase 5-1의 시세 예측과 같은 접근). 실제 연동은 나중이다.
"""

from __future__ import annotations

import time
from functools import lru_cache
from typing import Any

import numpy as np

from app.core.ids import IdSpace, require_supported
from app.services.anomaly.dataset import build_timeline
from app.services.anomaly.detector import contributions, get_detector
from app.services.anomaly.exceptions import TradeNotFoundError
from app.services.anomaly.scenarios import ANOMALY_LABELS


@lru_cache
def _index_by_trade_id() -> dict[int, int]:
    trades, _, _ = build_timeline()
    return {trade["trade_id"]: index for index, trade in enumerate(trades)}


def detect_trade(tenant_code: str, trade_id: int, id_space: IdSpace) -> dict[str, Any]:
    # 어느 데이터 평면의 id인지 호출자가 밝혀야 한다. 합성 코퍼스(1~26,702)와
    # 백엔드 거래(1~)의 범위가 겹쳐서, 검사 없이 받으면 엉뚱한 거래의 판정을
    # 조용히 돌려주게 된다. app/core/ids.py 참고.
    #
    # **id_space에 기본값을 두지 않는다.** 예전엔 SYNTHETIC이 기본이었는데,
    # 그러면 인자를 빠뜨린 새 호출자가 조용히 합성 데이터로 답을 받는다 —
    # 이 함수가 막으려는 바로 그 실패 방식이다. 라우터는 처음부터 기본값을
    # 두지 않았으므로, 함수 쪽 기본값은 그 의도와 어긋나 있었다(ADR-0022).
    # tests/test_id_space.py 가 시그니처를 고정한다.
    require_supported(id_space, "거래")

    detector = get_detector()
    threshold = detector.threshold(tenant_code)

    trades, features, _ = build_timeline()
    index = _index_by_trade_id().get(trade_id)
    if index is None:
        raise TradeNotFoundError(trade_id)

    started = time.perf_counter()
    scores, per_feature = detector.score(features[index : index + 1])
    elapsed = round((time.perf_counter() - started) * 1000, 1)

    return {
        **_summarize(trades[index], float(scores[0]), per_feature[0], threshold),
        "alert_percentile": detector.alert_percentile,
        "timings": {"scoring_ms": elapsed},
    }


def list_alerts(tenant_code: str, limit: int = 10) -> dict[str, Any]:
    """임계값을 넘은 거래를 점수 내림차순으로. GM 검토 큐에 해당한다.

    **이 경로는 `require_supported()`를 지나지 않는다.** 외부에서 id를 받지
    않고 합성 코퍼스(`build_timeline()`)만 훑기 때문에, 지금은 검사할 대상이
    없다 — 호출자가 공간을 지목할 여지 자체가 없다.

    이 전제가 깨지는 순간이 있다: **이 목록에 백엔드 거래가 섞이면** 그때는
    거래마다 공간이 달라지므로 `_summarize()`의 `id_space` 하드코딩이 곧바로
    거짓이 되고, 화면은 합성 유저 번호를 실제 번호처럼 보여주게 된다.
    그때 이 함수는 거래별로 공간을 들고 다니게 바뀌어야 한다(ADR-0022).
    """
    detector = get_detector()
    threshold = detector.threshold(tenant_code)

    trades, features, _ = build_timeline()
    scores, per_feature = detector.score(features)

    flagged = np.flatnonzero(scores > threshold)
    ranked = flagged[np.argsort(scores[flagged])[::-1][:limit]]

    return {
        "tenant_code": tenant_code,
        "threshold": round(threshold, 4),
        "alert_percentile": detector.alert_percentile,
        "total_trades": len(trades),
        "total_alerts": int(len(flagged)),
        "alerts": [
            _summarize(trades[i], float(scores[i]), per_feature[i], threshold)
            for i in ranked
        ],
    }


def _summarize(
    trade: dict[str, Any],
    score: float,
    per_feature: np.ndarray,
    threshold: float,
) -> dict[str, Any]:
    return {
        "trade_id": trade["trade_id"],
        "item_id": trade["item_id"],
        "buyer_id": trade["buyer_id"],
        "seller_id": trade["seller_id"],
        # 이 id들이 어느 평면 소속인지 명시한다. buyer/seller는 합성 유저
        # (1~206)라 백엔드 유저(1~5)와 겹치면서도 다른 사람이다. 화면이
        # 이걸 백엔드 id처럼 보여주면 사용자가 다른 곳에 입력하게 된다.
        "id_space": IdSpace.SYNTHETIC.value,
        "price": trade["price"],
        "quantity": trade["quantity"],
        "market_median": trade["market_median"],
        "price_ratio": round(trade["price"] / max(trade["market_median"], 1.0), 3),
        "traded_at": trade["traded_at"].isoformat(),
        "anomaly_score": round(score, 4),
        "threshold": round(threshold, 4),
        "is_anomaly": bool(score > threshold),
        "contributions": contributions(per_feature),
        # 더미 데이터라 주입 시나리오의 정답 라벨을 알고 있다. 실서비스에는
        # 없는 필드이고, 데모에서 판정이 맞았는지 눈으로 보라고 남긴다.
        "injected_label": ANOMALY_LABELS.get(trade.get("anomaly_type") or ""),
    }
