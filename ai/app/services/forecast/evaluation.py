"""예측 평가 지표와 나이브 기준선.

더미 거래 이력은 패턴을 알고 만든 합성 데이터라서, 모델 단독 MAPE만 보면
"모델이 잘한다"고 착각하기 쉽다. 시세 예측에서 직전값 유지(random walk)는
악명 높게 이기기 어려운 기준선이므로, 항상 같이 재서 **모델이 기준선을
실제로 이겼는지**로 판단한다. Phase 4에서 세운 원칙(수치가 나오면 그
수치를 깨려고 시도한다)의 연장이다.
"""

from __future__ import annotations

import numpy as np


def mape(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """평균 절대 백분율 오차(%). 타깃이 비율이라 0 근처가 아니라 안전하다."""
    return float(np.mean(np.abs((y_true - y_pred) / y_true)) * 100.0)


def signal_correlation(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """예측 편차와 실제 편차의 상관계수.

    MAPE만 보면 모델이 전부 1.0(=직전값 유지)을 뱉어도 기준선과 비슷한 점수가
    나와서 붕괴를 못 잡아낸다. "가격이 변할 방향"을 실제로 맞히는지는 1.0에서의
    편차끼리 상관을 봐야 드러난다. 0에 가까우면 모델이 아무것도 못 배운 것.
    """
    deviation_true = (y_true - 1.0).ravel()
    deviation_pred = (y_pred - 1.0).ravel()
    if deviation_pred.std() < 1e-8:
        return 0.0
    return float(np.corrcoef(deviation_pred, deviation_true)[0, 1])


def naive_last(x: np.ndarray, horizon: int) -> np.ndarray:
    """직전값 유지: 앞으로도 지금 가격 그대로.

    피처가 이미 마지막 가격 기준 비율이므로 예측값은 전부 1.0이다.
    """
    return np.ones((len(x), horizon), dtype=np.float32)


def naive_drift(x: np.ndarray, horizon: int, lookback: int = 7) -> np.ndarray:
    """선형 외삽: 최근 lookback일의 하루 평균 변화량을 그대로 연장."""
    prices = x[:, :, 0]
    span = min(lookback, prices.shape[1] - 1)
    daily_delta = (prices[:, -1] - prices[:, -1 - span]) / span
    steps = np.arange(1, horizon + 1, dtype=np.float32)
    return (prices[:, -1:] + daily_delta[:, None] * steps[None, :]).astype(np.float32)
