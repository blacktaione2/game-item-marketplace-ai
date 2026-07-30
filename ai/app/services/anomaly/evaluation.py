"""이상 탐지 평가 지표와 규칙 기준선.

Phase 5-1에서 나이브 기준선을 둔 것과 같은 역할이다. 오토인코더의 유일한
우위는 **피처 간 상호작용**을 본다는 점이므로, 그 우위를 정확히 분리해내는
통제군을 둔다.

- `price_zscore` : 가격 하나만 본다. "그냥 시세 대비만 봐도 되는 것 아닌가"
- `max_abs_z`    : 전 피처의 최대 |z|. 다변량이지만 **상호작용은 못 본다**

두 번째가 핵심이다. 오토인코더가 max_abs_z를 못 이기면 이 컴포넌트는 규칙
대비 이점이 없는 것이고, 그러면 그렇게 보고해야 한다.

지표는 PR-AUC와 **유형별 재현율**을 같이 본다. ROC-AUC는 이상거래처럼 극단적
불균형에서 낙관적으로 나오므로 쓰지 않는다.
"""

from __future__ import annotations

import numpy as np
from sklearn.metrics import average_precision_score

from app.services.anomaly.features import FEATURE_NAMES

PRICE_FEATURE_INDEX = FEATURE_NAMES.index("log_price_ratio")


def price_zscore(scaled_x: np.ndarray) -> np.ndarray:
    """가격 단일 피처 기준선. 입력이 이미 중앙값/IQR 정규화라 그대로 |z|다."""
    return np.abs(scaled_x[:, PRICE_FEATURE_INDEX])


def max_abs_z(scaled_x: np.ndarray) -> np.ndarray:
    """전 피처 최대 |z| 기준선. 다변량이되 축을 하나씩 따로 본다."""
    return np.abs(scaled_x).max(axis=1)


def pr_auc(labels: np.ndarray, scores: np.ndarray) -> float:
    return float(average_precision_score(labels, scores))


def recall_by_type(
    labels: np.ndarray,
    scores: np.ndarray,
    types: list[str | None],
    budget: int,
) -> tuple[dict[str, tuple[int, int]], float]:
    """알림 예산(상위 budget건) 안에서 유형별 (적발, 전체)과 정밀도.

    유형별 재현율은 기저율에 흔들리지 않아 방법 간 비교에 PR-AUC보다 안정적이다.
    """
    flagged = np.zeros(len(scores), dtype=bool)
    flagged[np.argsort(scores)[::-1][:budget]] = True

    caught: dict[str, list[int]] = {}
    for index, anomaly_type in enumerate(types):
        if anomaly_type is None:
            continue
        entry = caught.setdefault(anomaly_type, [0, 0])
        entry[1] += 1
        if flagged[index]:
            entry[0] += 1

    true_positives = int((flagged & (labels == 1)).sum())
    precision = true_positives / budget if budget else 0.0
    return {key: (value[0], value[1]) for key, value in caught.items()}, precision
