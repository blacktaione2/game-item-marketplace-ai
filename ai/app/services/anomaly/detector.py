"""학습된 오토인코더 서빙 + 피처 기여도 분해.

기여도 분해가 이 컴포넌트의 존재 이유다(Isolation Forest 대신 오토인코더를
고른 유일한 근거). "이상 점수 11.3"이 아니라 "이상함 — 기여도의 58%가 판매자
24시간 거래 건수"라고 말할 수 있어야 GM이 검토 큐에서 바로 판단한다.

기여도는 **재구성 오차에 대한 몫**이지 인과적 귀인이 아니다. 피처들이 서로
상관되어 있으면 몫이 여러 축으로 흩어질 수 있다.
"""

from __future__ import annotations

import threading
from functools import lru_cache
from typing import Any

import numpy as np

from app.core.config import get_settings
from app.services.anomaly.exceptions import AnomalyModelNotTrainedError, UnknownTenantError
from app.services.anomaly.features import (
    FEATURE_LABELS,
    FEATURE_NAMES,
    RobustScaler,
)
from app.services.anomaly.model import is_trained, load_model, squared_errors


class AnomalyDetector:
    def __init__(self, model_dir: str) -> None:
        self._model_dir = model_dir
        self._model = None
        self._config: dict[str, Any] = {}
        self._scaler: RobustScaler | None = None
        self._lock = threading.Lock()

    def _ensure_loaded(self) -> None:
        if self._model is None:
            with self._lock:
                if self._model is None:
                    if not is_trained(self._model_dir):
                        raise AnomalyModelNotTrainedError(self._model_dir)
                    self._model, self._config = load_model(self._model_dir)
                    self._scaler = RobustScaler.from_dict(self._config["scaler"])

    def threshold(self, tenant_code: str) -> float:
        self._ensure_loaded()
        thresholds = self._config["thresholds"]
        if tenant_code not in thresholds:
            raise UnknownTenantError(tenant_code, sorted(thresholds))
        return float(thresholds[tenant_code])

    @property
    def alert_percentile(self) -> float:
        self._ensure_loaded()
        return float(self._config["alert_percentile"])

    def score(self, raw_features: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """(n, FEATURE_DIM) 원시 피처 → (이상 점수, 피처별 제곱오차)."""
        self._ensure_loaded()
        assert self._scaler is not None
        scaled = self._scaler.transform(raw_features)
        per_feature = squared_errors(self._model, scaled)
        return per_feature.sum(axis=1), per_feature


def contributions(per_feature: np.ndarray, top_k: int = 3) -> list[dict[str, Any]]:
    """피처별 제곱오차 → 사람이 읽을 기여도 상위 top_k.

    hour_sin/hour_cos처럼 한 개념을 두 축으로 인코딩한 피처는 합쳐서 보여준다.
    쪼개 보여주면 "체결 시각"이 두 줄로 나와서 읽는 사람이 헷갈린다.
    """
    total = float(per_feature.sum())
    if total <= 0:
        return []

    merged: dict[str, float] = {}
    for name, value in zip(FEATURE_NAMES, per_feature):
        label = FEATURE_LABELS[name]
        merged[label] = merged.get(label, 0.0) + float(value)

    ranked = sorted(merged.items(), key=lambda kv: kv[1], reverse=True)[:top_k]
    return [
        {"feature": label, "share": round(value / total, 4)} for label, value in ranked
    ]


@lru_cache
def get_detector() -> AnomalyDetector:
    return AnomalyDetector(get_settings().anomaly_model_dir)
