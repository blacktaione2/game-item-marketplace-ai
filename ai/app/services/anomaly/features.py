"""거래 → 피처 벡터.

원칙은 Phase 5-1과 같다: **원시값이 아니라 맥락 대비 상대값.** 오토인코더는
"체결가 50만원"만 보고 그게 이 아이템 기준 비싼지 알 수 없다. 시세 대비
비율로 바꿔야 아이템 가격대와 무관한 이상 신호가 된다.

행동 기반 피처(24시간 거래 건수, 상대별 7일 반복 횟수)는 **시간순 한 번의
패스로 과거만 보고** 계산한다. 미래를 보면 그 자체가 누수다.

스케일링은 StandardScaler가 아니라 중앙값/IQR 기반이다. 학습셋에 이상치가
섞일 수 있는데 평균·표준편차는 거기에 끌려간다.
"""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timedelta
from typing import Any

import numpy as np

FEATURE_NAMES = [
    "log_price_ratio",  # 시세 대비 체결가
    "log_amount",  # 거래 규모
    "log_quantity",  # 수량
    "hour_sin",  # 체결 시각(주기)
    "hour_cos",
    "log_buyer_age",  # 구매자 계정 나이
    "log_seller_age",  # 판매자 계정 나이
    "log_buyer_trades_24h",  # 구매자 24시간 거래 건수
    "log_seller_trades_24h",  # 판매자 24시간 거래 건수
    "log_pair_trades_7d",  # 동일 구매자-판매자 쌍 7일 반복
    "is_auction",  # 경매 여부
]

# 사람이 읽을 이름 — 피처 기여도 설명에 그대로 나간다.
FEATURE_LABELS = {
    "log_price_ratio": "시세 대비 체결가",
    "log_amount": "거래 금액",
    "log_quantity": "수량",
    "hour_sin": "체결 시각",
    "hour_cos": "체결 시각",
    "log_buyer_age": "구매자 계정 나이",
    "log_seller_age": "판매자 계정 나이",
    "log_buyer_trades_24h": "구매자 24시간 거래 건수",
    "log_seller_trades_24h": "판매자 24시간 거래 건수",
    "log_pair_trades_7d": "동일 상대 7일 반복 거래",
    "is_auction": "경매 여부",
}

FEATURE_DIM = len(FEATURE_NAMES)


def extract_features(
    trades: list[dict[str, Any]], users_by_id: dict[int, dict[str, Any]]
) -> np.ndarray:
    """체결 시각 오름차순 거래 리스트 → (n, FEATURE_DIM) 원시 피처.

    입력이 시간순으로 정렬돼 있어야 한다 — 롤링 카운트가 그 전제 위에 있다.
    """
    buyer_window: dict[int, deque[datetime]] = defaultdict(deque)
    seller_window: dict[int, deque[datetime]] = defaultdict(deque)
    pair_window: dict[tuple[int, int], deque[datetime]] = defaultdict(deque)

    rows = np.empty((len(trades), FEATURE_DIM), dtype=np.float32)

    for index, trade in enumerate(trades):
        traded_at = trade["traded_at"]

        buyer_count = _rolling_count(
            buyer_window[trade["buyer_id"]], traded_at, timedelta(hours=24)
        )
        seller_count = _rolling_count(
            seller_window[trade["seller_id"]], traded_at, timedelta(hours=24)
        )
        pair_count = _rolling_count(
            pair_window[(trade["buyer_id"], trade["seller_id"])],
            traded_at,
            timedelta(days=7),
        )

        buyer_age = _account_age_days(users_by_id, trade["buyer_id"], traded_at)
        seller_age = _account_age_days(users_by_id, trade["seller_id"], traded_at)

        hour = traded_at.hour + traded_at.minute / 60.0
        angle = 2 * np.pi * hour / 24.0

        rows[index] = (
            np.log(max(trade["price"], 1.0) / max(trade["market_median"], 1.0)),
            np.log1p(trade["price"] * trade["quantity"]),
            np.log1p(trade["quantity"]),
            np.sin(angle),
            np.cos(angle),
            np.log1p(buyer_age),
            np.log1p(seller_age),
            np.log1p(buyer_count),
            np.log1p(seller_count),
            np.log1p(pair_count),
            1.0 if trade["trade_type"] == "BID" else 0.0,
        )

    return rows


def _rolling_count(
    window: deque[datetime], now: datetime, span: timedelta
) -> int:
    """이 거래 **직전까지**의 건수를 세고, 자기 자신을 창에 넣는다."""
    while window and (now - window[0]) > span:
        window.popleft()
    count = len(window)
    window.append(now)
    return count


def _account_age_days(
    users_by_id: dict[int, dict[str, Any]], user_id: int, at: datetime
) -> float:
    created_at = users_by_id[user_id]["created_at"]
    return max((at - created_at).total_seconds() / 86400.0, 0.0)


class RobustScaler:
    """중앙값/IQR 정규화. 이상치에 끌려가지 않는다."""

    def __init__(self, center: np.ndarray, scale: np.ndarray) -> None:
        self.center = center
        self.scale = scale

    @classmethod
    def fit(cls, x: np.ndarray) -> RobustScaler:
        center = np.median(x, axis=0)
        iqr = np.percentile(x, 75, axis=0) - np.percentile(x, 25, axis=0)
        # IQR이 0인 피처(상수에 가까운 축)에서 0으로 나누지 않도록.
        scale = np.where(iqr < 1e-6, 1.0, iqr)
        return cls(center.astype(np.float32), scale.astype(np.float32))

    def transform(self, x: np.ndarray) -> np.ndarray:
        return ((x - self.center) / self.scale).astype(np.float32)

    def to_dict(self) -> dict[str, list[float]]:
        return {"center": self.center.tolist(), "scale": self.scale.tolist()}

    @classmethod
    def from_dict(cls, payload: dict[str, list[float]]) -> RobustScaler:
        return cls(
            np.array(payload["center"], dtype=np.float32),
            np.array(payload["scale"], dtype=np.float32),
        )
