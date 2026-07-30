"""시계열 → 학습 윈도우 변환.

핵심은 **윈도우 마지막 가격으로 나눠 비율로 정규화**하는 것이다. 3천원짜리
물약과 85만원짜리 계정을 하나의 모델로 다루려면 절대 가격을 그대로 넣을 수
없다. 비율로 바꾸면 모델이 배우는 건 "다음 7일 동안 몇 배가 되는가"가 되고,
이 표현이 그대로 Cold Start 트렌드 상속의 근거가 된다 — 이력이 없는 아이템도
비슷한 아이템의 *비율* 궤적은 물려받을 수 있기 때문이다.
"""

from __future__ import annotations

from typing import Any

import numpy as np

# 피처 축: [가격 비율, 거래량 비율]
FEATURE_DIM = 2


def to_arrays(series: list[dict[str, Any]]) -> tuple[np.ndarray, np.ndarray]:
    prices = np.array([point["price"] for point in series], dtype=np.float32)
    volumes = np.array([point["volume"] for point in series], dtype=np.float32)
    return prices, volumes


def normalize_window(prices: np.ndarray, volumes: np.ndarray) -> np.ndarray:
    """(window,) 두 배열 → (window, 2) 정규화 피처.

    가격은 윈도우 마지막 값 기준 비율, 거래량은 윈도우 평균 기준 비율.

    앵커를 마지막 값 한 점으로 잡으면 나이브 기준선(직전값 유지)의 예측이
    정확히 1.0이 되어 모델이 기준선을 이겼는지 바로 읽힌다. 대신 그 한 건이
    튀면 예측 전체가 밀린다 — 실거래 전환 시 스무딩을 검토할 것
    (ADR-0008 알려진 한계 1).
    """
    anchor = float(prices[-1])
    volume_mean = max(float(volumes.mean()), 1.0)
    return np.stack([prices / anchor, volumes / volume_mean], axis=1).astype(np.float32)


def build_windows(
    prices: np.ndarray, volumes: np.ndarray, window: int, horizon: int
) -> tuple[np.ndarray, np.ndarray]:
    """한 아이템의 시리즈를 (X, Y)로 자른다.

    X: (n, window, 2) 정규화 피처
    Y: (n, horizon)   윈도우 마지막 가격 대비 미래 가격 비율
    """
    features: list[np.ndarray] = []
    targets: list[np.ndarray] = []
    last_start = len(prices) - window - horizon
    for start in range(last_start + 1):
        end = start + window
        window_prices = prices[start:end]
        anchor = float(window_prices[-1])
        features.append(normalize_window(window_prices, volumes[start:end]))
        targets.append(prices[end : end + horizon] / anchor)

    if not features:
        empty_x = np.empty((0, window, FEATURE_DIM), dtype=np.float32)
        return empty_x, np.empty((0, horizon), dtype=np.float32)
    return np.stack(features), np.stack(targets).astype(np.float32)


def build_dataset(
    series_by_item: dict[int, list[dict[str, Any]]],
    window: int,
    horizon: int,
    val_ratio: float = 0.2,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """아이템별 시계열 묶음 → 학습/검증 배열.

    분할은 **아이템별 시간순**이다. 무작위 분할을 하면 같은 아이템의 미래
    구간이 학습에 섞여 검증 점수가 부풀려진다(시계열에서 가장 흔한 누수).
    """
    train_x, train_y, val_x, val_y = [], [], [], []
    for item_id in sorted(series_by_item):
        prices, volumes = to_arrays(series_by_item[item_id])
        x, y = build_windows(prices, volumes, window, horizon)
        if len(x) == 0:
            continue
        split = int(len(x) * (1.0 - val_ratio))
        train_x.append(x[:split])
        train_y.append(y[:split])
        val_x.append(x[split:])
        val_y.append(y[split:])

    if not train_x:
        raise ValueError("학습 윈도우를 하나도 만들지 못했습니다 — 이력 길이를 확인하세요")

    return (
        np.concatenate(train_x),
        np.concatenate(train_y),
        np.concatenate(val_x),
        np.concatenate(val_y),
    )
