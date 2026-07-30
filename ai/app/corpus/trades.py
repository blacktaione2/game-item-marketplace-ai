"""개별 거래 + 유저 풀 (Phase 5-2 더미 데이터).

Phase 5-1의 `trade_history.py`는 **일별 집계**라 이상거래 탐지에는 못 쓴다.
여기서는 그 일별 시리즈를 개별 체결 건으로 풀어낸다 — 그 날의 `volume`만큼
거래를 만들고 가격을 그 날 평균가 주변에 흩뿌린다. 두 Phase 5 모델이 같은
합성 세계를 공유하게 되고, "시세 대비 비율" 피처가 LSTM이 쓰는 바로 그
시리즈를 참조하게 된다.

## 유저 풀에 반복 거래 상대를 일부러 넣는 이유

평가에서 가장 중요한 시나리오는 "복합 미세 이상"(모든 피처가 개별로는 정상
범위인데 조합이 이상한 거래)이다. 그런데 **정상 거래에 반복 상대가 전혀
없으면** `pair_trades_7d`가 0/1로만 갈려서 그 피처 하나로 이상치가 완전히
분리돼버린다. 그러면 "단일 피처로는 못 잡는다"는 평가 설계 자체가 무의미해진다.

그래서 유저마다 단골(친구/길드) 상대를 두고, 정상 거래의 일정 비율이 그
사이에서 일어나게 한다. 목표는 정상 거래의 `pair_trades_7d`가 1~5 사이에
자연스럽게 퍼지는 것이다.
"""

from __future__ import annotations

import bisect
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Any

import numpy as np

from app.corpus import ALL_ITEMS
from app.corpus.trade_history import HISTORY_SPECS, SERIES_END, get_price_series

TENANT_ID = 1
SEED = 20260729

USER_COUNT = 150
PARTNERS_PER_USER = 3
# 정상 거래 중 단골 상대와 일어나는 비율
REPEAT_TRADE_RATIO = 0.35

# 게임 거래소 접속 패턴 — 저녁에 몰리고 새벽이 가장 한산하다.
# 새벽 거래가 드물어야 "새벽 + 다른 신호" 조합이 의미를 갖는다.
HOUR_WEIGHTS = np.array(
    [
        # 0시 ~ 5시
        2.0, 1.2, 0.7, 0.5, 0.4, 0.5,
        # 6시 ~ 11시
        0.9, 1.5, 2.2, 2.8, 3.2, 3.5,
        # 12시 ~ 17시
        4.0, 3.8, 3.5, 3.6, 4.0, 4.5,
        # 18시 ~ 23시
        5.5, 7.0, 8.5, 9.0, 7.5, 4.5,
    ]
)

_ITEMS_BY_ID = {item["item_id"]: item for item in ALL_ITEMS}


def _build_users(rng: np.random.Generator) -> list[dict[str, Any]]:
    # 계정 나이는 지수분포 — 오래된 계정이 다수이고 신규가 꼬리를 이룬다.
    # 정상 데이터에도 신규 계정이 충분히 있어야 "신규 계정" 신호 하나만으로
    # 이상치가 분리되지 않는다.
    ages = np.clip(rng.exponential(320.0, USER_COUNT).astype(int) + 1, 1, 1500)
    users = [
        {
            "user_id": index + 1,
            "tenant_id": TENANT_ID,
            "created_at": datetime.combine(SERIES_END, datetime.min.time())
            - timedelta(days=int(age)),
            "partners": [],
        }
        for index, age in enumerate(ages)
    ]

    # 단골 관계는 양방향으로 맺는다 — 한쪽만 알고 있는 친구는 없다.
    for user in users:
        while len(user["partners"]) < PARTNERS_PER_USER:
            other = users[int(rng.integers(0, USER_COUNT))]
            if other["user_id"] == user["user_id"]:
                continue
            if other["user_id"] in user["partners"]:
                continue
            user["partners"].append(other["user_id"])
            if user["user_id"] not in other["partners"]:
                other["partners"].append(user["user_id"])

    return users


def _rolling_median_prices(item_id: int, window: int = 7) -> dict[str, float]:
    """아이템별 '최근 window일 중앙가'. 시세 대비 비율 피처의 분모."""
    series = get_price_series(item_id)
    prices = [point["price"] for point in series]
    medians: dict[str, float] = {}
    for index, point in enumerate(series):
        start = max(0, index - window + 1)
        medians[point["date"]] = float(np.median(prices[start : index + 1]))
    return medians


def _pick_counterparties(
    rng: np.random.Generator,
    users: list[dict[str, Any]],
    eligible_count: int,
    users_by_id: dict[int, dict[str, Any]],
) -> tuple[int, int]:
    """구매자·판매자를 고른다. 일정 비율은 단골 상대끼리 붙인다."""
    seller = users[int(rng.integers(0, eligible_count))]

    if rng.random() < REPEAT_TRADE_RATIO and seller["partners"]:
        partners = [
            pid
            for pid in seller["partners"]
            # 거래 시점에 이미 존재하는 계정만 — 미래 계정과 거래할 수는 없다.
            if users_by_id[pid]["_index"] < eligible_count
        ]
        if partners:
            buyer_id = int(partners[int(rng.integers(0, len(partners)))])
            return buyer_id, int(seller["user_id"])

    buyer = users[int(rng.integers(0, eligible_count))]
    if buyer["user_id"] == seller["user_id"]:
        buyer = users[(int(seller["_index"]) + 1) % eligible_count]
    return int(buyer["user_id"]), int(seller["user_id"])


@lru_cache
def build_corpus() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(유저 목록, 정상 거래 목록). 거래는 체결 시각 오름차순."""
    rng = np.random.default_rng(SEED)

    users = _build_users(rng)
    # 계정 생성 시각 오름차순 정렬 — 거래 시점에 존재하는 계정만 고르기 위함.
    users.sort(key=lambda u: u["created_at"])
    for index, user in enumerate(users):
        user["_index"] = index
    users_by_id = {user["user_id"]: user for user in users}
    created_ats = [user["created_at"] for user in users]

    hour_probs = HOUR_WEIGHTS / HOUR_WEIGHTS.sum()

    trades: list[dict[str, Any]] = []
    for item_id in sorted(HISTORY_SPECS):
        item = _ITEMS_BY_ID[item_id]
        medians = _rolling_median_prices(item_id)
        is_stackable = item["category"] in ("소모품", "재화")

        for point in get_price_series(item_id):
            day = datetime.fromisoformat(point["date"])
            for _ in range(point["volume"]):
                hour = int(rng.choice(24, p=hour_probs))
                traded_at = day + timedelta(
                    hours=hour,
                    minutes=int(rng.integers(0, 60)),
                    seconds=int(rng.integers(0, 60)),
                )
                eligible_count = bisect.bisect_right(created_ats, traded_at)
                if eligible_count < 2:
                    continue

                buyer_id, seller_id = _pick_counterparties(
                    rng, users, eligible_count, users_by_id
                )
                # 개별 체결가는 그 날 평균가 주변에 흩어진다.
                price = float(point["price"] * np.exp(rng.normal(0.0, 0.03)))
                trades.append(
                    {
                        "tenant_id": TENANT_ID,
                        "item_id": item_id,
                        "buyer_id": buyer_id,
                        "seller_id": seller_id,
                        "trade_type": (
                            "BID" if item["sale_type"] == "AUCTION" else "PURCHASE"
                        ),
                        "price": round(price, -1),
                        "quantity": (
                            int(rng.integers(1, 6)) if is_stackable else 1
                        ),
                        "traded_at": traded_at,
                        "market_median": medians[point["date"]],
                        "anomaly_type": None,
                    }
                )

    trades.sort(key=lambda t: t["traded_at"])
    for index, trade in enumerate(trades):
        trade["trade_id"] = index + 1
    return users, trades


def normal_trades() -> list[dict[str, Any]]:
    return build_corpus()[1]


def users() -> list[dict[str, Any]]:
    return build_corpus()[0]


def market_median(item_id: int, day: str) -> float:
    """해당 아이템의 그 날짜 기준 최근 7일 중앙가."""
    return _rolling_median_prices(item_id)[day]
