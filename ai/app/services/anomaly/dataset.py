"""정상 데이터 3분할 + 평가셋 조립.

## 왜 정상 데이터를 세 갈래로 나누는가

오토인코더는 자기가 학습한 데이터를 가장 잘 재구성한다. 그래서 **학습셋
재구성 오차의 99 백분위수를 임계값으로 쓰면 낙관적으로 편향된다** — 운영에서
마주치는 처음 보는 정상 거래는 오차가 전반적으로 더 높게 나오므로, 예상한
1%보다 훨씬 많은 알림이 터진다.

- `train`     : 모델 학습에만 쓴다
- `threshold` : 학습에 안 쓴 정상 데이터. **임계값은 여기서 잡는다**
- `eval`      : 위 둘과 별개. 정상 + 주입 이상치를 섞어 성능을 잰다

분할은 시간순이다. 24시간/7일 롤링 피처가 있어서 무작위 분할은 누수가 된다.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from functools import lru_cache
from typing import Any, NamedTuple

import numpy as np

from app.corpus.trade_history import SERIES_END
from app.corpus.trades import build_corpus
from app.services.anomaly.features import RobustScaler, extract_features
from app.services.anomaly.scenarios import build_anomalies

TRAIN_RATIO = 0.70
THRESHOLD_RATIO = 0.15
# 나머지 0.15가 평가 구간


class Splits(NamedTuple):
    train: np.ndarray
    threshold: np.ndarray
    eval_x: np.ndarray
    eval_labels: np.ndarray  # 1 = 이상
    eval_types: list[str | None]
    eval_trades: list[dict[str, Any]]
    scaler: RobustScaler
    users_by_id: dict[int, dict[str, Any]]


def split_boundaries() -> tuple[datetime, datetime]:
    end = datetime.combine(SERIES_END, datetime.max.time())
    start = end - timedelta(days=120)
    train_end = start + timedelta(days=120 * TRAIN_RATIO)
    threshold_end = train_end + timedelta(days=120 * THRESHOLD_RATIO)
    return train_end, threshold_end


@lru_cache
def build_timeline(
    anomaly_seed: int = 777,
) -> tuple[list[dict[str, Any]], np.ndarray, dict[int, dict[str, Any]]]:
    """정상 + 주입 이상치를 합친 전체 타임라인과 그 원시 피처.

    롤링 피처는 반드시 전체 타임라인 위에서 계산해야 한다. 평가 구간 거래의
    "24시간 내 건수"는 그 이전 구간까지 거슬러 봐야 맞는 값이 나온다.
    """
    users, normal = build_corpus()
    _, threshold_end = split_boundaries()
    eval_end = datetime.combine(SERIES_END, datetime.max.time())

    extra_users, extra_trades = build_anomalies(
        existing_user_ids=[user["user_id"] for user in users],
        eval_start=threshold_end,
        eval_end=eval_end,
        seed=anomaly_seed,
    )

    users_by_id = {user["user_id"]: user for user in [*users, *extra_users]}
    merged = sorted([*normal, *extra_trades], key=lambda t: t["traded_at"])
    # 주입분까지 합친 뒤 id를 다시 매긴다 — 서빙과 평가가 같은 id를 본다.
    for index, trade in enumerate(merged):
        trade["trade_id"] = index + 1

    return merged, extract_features(merged, users_by_id), users_by_id


def build_splits(anomaly_seed: int = 777) -> Splits:
    train_end, threshold_end = split_boundaries()
    merged, features, users_by_id = build_timeline(anomaly_seed)

    times = np.array([t["traded_at"] for t in merged])
    is_train = times <= train_end
    is_threshold = (times > train_end) & (times <= threshold_end)
    is_eval = times > threshold_end

    # 스케일러는 학습셋에만 적합시킨다 — 임계값/평가 구간을 보면 그것도 누수다.
    scaler = RobustScaler.fit(features[is_train])

    eval_trades = [trade for trade, keep in zip(merged, is_eval) if keep]
    return Splits(
        train=scaler.transform(features[is_train]),
        threshold=scaler.transform(features[is_threshold]),
        eval_x=scaler.transform(features[is_eval]),
        eval_labels=np.array(
            [1 if trade["anomaly_type"] else 0 for trade in eval_trades]
        ),
        eval_types=[trade["anomaly_type"] for trade in eval_trades],
        eval_trades=eval_trades,
        scaler=scaler,
        users_by_id=users_by_id,
    )
