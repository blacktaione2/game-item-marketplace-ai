"""학습된 예측 모델 래퍼.

임베딩/리랭커와 동일하게 최초 사용 시점에 지연 로딩한다(공유 인프라 제약).
"""

from __future__ import annotations

import threading
from functools import lru_cache

import numpy as np

from app.core.config import get_settings
from app.services.forecast.exceptions import ForecastModelNotTrainedError
from app.services.forecast.model import is_trained, load_model


class ForecastService:
    def __init__(self, model_dir: str) -> None:
        self._model_dir = model_dir
        self._model = None
        self._config: dict[str, int] = {}
        self._lock = threading.Lock()

    def _get_model(self):
        if self._model is None:
            with self._lock:
                if self._model is None:
                    if not is_trained(self._model_dir):
                        raise ForecastModelNotTrainedError(self._model_dir)
                    self._model, self._config = load_model(self._model_dir)
        return self._model

    @property
    def window(self) -> int:
        self._get_model()
        return self._config["window"]

    @property
    def horizon(self) -> int:
        self._get_model()
        return self._config["horizon"]

    def predict_ratios(self, features: np.ndarray) -> np.ndarray:
        """(window, 2) 정규화 피처 → (horizon,) 가격 비율."""
        import torch

        model = self._get_model()
        with torch.no_grad():
            batch = torch.from_numpy(features[None, :, :])
            return model(batch)[0].numpy()


@lru_cache
def get_forecast_service() -> ForecastService:
    return ForecastService(get_settings().forecast_model_dir)
