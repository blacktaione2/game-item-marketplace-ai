"""이상거래 시나리오 주입 (평가 전용).

## 피처가 아니라 행동을 심는다

`seller_trades_24h = 50` 처럼 피처 값을 직접 세팅하면 순환논증이 된다 —
내가 만든 숫자를 내가 되찾는 것뿐이다. 그래서 여기서는 **거래 자체를**
만든다. 작업장이면 같은 판매자의 거래 50건을 24시간 안에 실제로 생성하고,
`seller_trades_24h`는 features.py가 그걸 보고 파생시킨다.

## 난이도를 일부러 세 층으로 나눈다

정상 데이터의 실측 분포(26,489건)에 맞춰 각 시나리오의 극단성을 정했다.

| 시나리오 | 이탈 축 | 단일 피처로 분리되는가 |
|---|---|---|
| 자전거래 | 가격(정상 최대 1.31배 → 4~12배) | 분리됨 |
| 계정 도용 투매 | 가격(정상 최저 0.87배 → 0.05~0.2배) | 분리됨 |
| 작업장 | 24시간 건수(정상 최대 12 → 40~45) | 분리됨 |
| 대포 계정 | 계정 나이(정상 p1=3.9일 → 1일 미만) | 분리됨 |
| 복합 미세 | 없음 — **모든 축이 정상 p80~p95 이내** | **분리 안 됨** |

앞의 네 개는 규칙 기준선도 잡아야 정상이다. 오토인코더가 값을 하는지는
맨 아래에서 갈린다. 못 잡으면 이 컴포넌트는 규칙 대비 이점이 없는 것이고,
그 결과가 나오면 그대로 보고한다.

실측 결과는 예상보다 갈렸다. 단일 축이 극단이어도 그 값이 **학습 분포에
표현돼 있으면** 오토인코더는 그대로 재구성해버려 놓친다(대포 계정: 계정
나이를 105% 따라 재구성, 재현율 12% vs 규칙 100%). 재구성 오차는 "희귀함"이
아니라 "재현 불가능함"을 재기 때문이다. 자세한 것은 ADR-0009.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import numpy as np

from app.corpus import ALL_ITEMS
from app.corpus.trade_history import HISTORY_SPECS
from app.corpus.trades import TENANT_ID, market_median

ANOMALY_TYPES = (
    "wash_trading",
    "stolen_dump",
    "rmt_farming",
    "mule_account",
    "subtle_composite",
)

ANOMALY_LABELS = {
    "wash_trading": "자전거래",
    "stolen_dump": "계정 도용 투매",
    "rmt_farming": "작업장 대량 유통",
    "mule_account": "대포 계정 고액거래",
    "subtle_composite": "복합 미세 이상",
}

_ITEMS_BY_ID = {item["item_id"]: item for item in ALL_ITEMS}
_HISTORY_ITEM_IDS = sorted(HISTORY_SPECS)


def _make_trade(
    rng: np.random.Generator,
    item_id: int,
    buyer_id: int,
    seller_id: int,
    traded_at: datetime,
    price_ratio: float,
    anomaly_type: str | None,
    quantity: int = 1,
) -> dict[str, Any]:
    item = _ITEMS_BY_ID[item_id]
    median = market_median(item_id, traded_at.date().isoformat())
    return {
        "tenant_id": TENANT_ID,
        "item_id": item_id,
        "buyer_id": buyer_id,
        "seller_id": seller_id,
        "trade_type": "BID" if item["sale_type"] == "AUCTION" else "PURCHASE",
        "price": round(median * price_ratio, -1),
        "quantity": quantity,
        "traded_at": traded_at,
        "market_median": median,
        "anomaly_type": anomaly_type,
    }


def _new_user(user_id: int, created_at: datetime) -> dict[str, Any]:
    return {
        "user_id": user_id,
        "tenant_id": TENANT_ID,
        "created_at": created_at,
        "partners": [],
    }


def _random_time(
    rng: np.random.Generator, day: datetime, hours: tuple[int, int]
) -> datetime:
    return day.replace(hour=0, minute=0, second=0) + timedelta(
        hours=float(rng.uniform(*hours)), minutes=float(rng.integers(0, 60))
    )


def build_anomalies(
    existing_user_ids: list[int],
    eval_start: datetime,
    eval_end: datetime,
    seed: int = 777,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(추가 유저, 추가 거래). 거래에는 정상으로 라벨된 셋업 거래가 섞여 있다."""
    rng = np.random.default_rng(seed)
    span_days = max((eval_end - eval_start).days - 8, 1)
    next_user_id = max(existing_user_ids) + 1

    users: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []

    def pick_day(offset_limit: int = 0) -> datetime:
        return eval_start + timedelta(
            days=int(rng.integers(0, max(span_days - offset_limit, 1)))
        )

    def pick_existing() -> int:
        return int(existing_user_ids[int(rng.integers(0, len(existing_user_ids)))])

    # --- 1. 자전거래: 같은 쌍이 시세 4~12배로 반복 체결 -----------------
    # 계정 간 자산 이전/돈세탁. 가격이 극단적이라 규칙으로도 잡힌다.
    for _ in range(2):
        buyer_id, seller_id = pick_existing(), pick_existing()
        if buyer_id == seller_id:
            continue
        item_id = int(rng.choice(_HISTORY_ITEM_IDS))
        start = pick_day(5)
        for burst_index in range(int(rng.integers(9, 13))):
            traded_at = start + timedelta(
                days=float(rng.uniform(0, 5)), hours=float(rng.uniform(0, 24))
            )
            trades.append(
                _make_trade(
                    rng,
                    item_id,
                    buyer_id,
                    seller_id,
                    traded_at,
                    float(rng.uniform(4.0, 12.0)),
                    "wash_trading",
                )
            )

    # --- 2. 계정 도용 투매: 새벽에 시세 5~20%로 급처분 -------------------
    for _ in range(1):
        seller_id = pick_existing()
        day = pick_day(1)
        for _ in range(int(rng.integers(18, 24))):
            item_id = int(rng.choice(_HISTORY_ITEM_IDS))
            traded_at = _random_time(rng, day, (2, 6))
            trades.append(
                _make_trade(
                    rng,
                    item_id,
                    pick_existing(),
                    seller_id,
                    traded_at,
                    float(rng.uniform(0.05, 0.20)),
                    "stolen_dump",
                )
            )

    # --- 3. 작업장: 신규 계정이 24시간에 40~60건 정상가로 유통 ------------
    for _ in range(1):
        day = pick_day(1)
        farmer = _new_user(next_user_id, day - timedelta(days=float(rng.uniform(1, 6))))
        users.append(farmer)
        next_user_id += 1
        # 24시간 내 건수 자체가 신호이므로 버스트 크기를 줄이지 않는다.
        # 대신 파머를 1명만 두어 평가셋 기저율이 왜곡되지 않게 한다.
        for _ in range(int(rng.integers(38, 46))):
            item_id = int(rng.choice(_HISTORY_ITEM_IDS))
            traded_at = day + timedelta(hours=float(rng.uniform(0, 24)))
            trades.append(
                _make_trade(
                    rng,
                    item_id,
                    pick_existing(),
                    farmer["user_id"],
                    traded_at,
                    # 시세대로 판다 — 가격 신호는 일부러 주지 않는다.
                    float(rng.uniform(0.95, 1.05)),
                    "rmt_farming",
                )
            )

    # --- 4. 대포 계정 고액거래: 생성 24시간 내 계정이 고액 매수 -----------
    # 계정 나이도 금액도 각각은 정상 분포의 꼬리에 존재한다. 조합이 신호다.
    expensive_items = sorted(
        _HISTORY_ITEM_IDS, key=lambda i: _ITEMS_BY_ID[i]["price"], reverse=True
    )[:6]
    for _ in range(25):
        day = pick_day()
        traded_at = day + timedelta(hours=float(rng.uniform(0, 24)))
        mule = _new_user(
            next_user_id, traded_at - timedelta(hours=float(rng.uniform(1, 20)))
        )
        users.append(mule)
        next_user_id += 1
        trades.append(
            _make_trade(
                rng,
                int(rng.choice(expensive_items)),
                mule["user_id"],
                pick_existing(),
                traded_at,
                float(rng.uniform(0.98, 1.12)),
                "mule_account",
            )
        )

    # --- 5. 복합 미세: 모든 축을 정상 p80~p95 밴드에 둔다 ------------------
    # 보정 기준을 결과보다 먼저 고정한다: **어느 축도 단독으로는 상위권에
    # 오르지 못할 만큼 평범해야 한다.** 정상 실측(26,489건) 기준으로
    #   가격배율   p50=1.00  p80~0.042  p90~0.065  p95=1.086  p99=1.167
    #   계정나이   p1=3.9일  p5=18.7일  p50=220일
    #   pair_7d    p90=2     p95=3      p99=5      max=14
    #   새벽 2~5시 전체의 2.4% (단 sin/cos 축에서는 p85~p90 수준)
    # 처음에는 가격을 p95~p99에 뒀는데, 그러면 가격 규칙 단독으로 63%가
    # 잡혀서 상호작용 검증이 성립하지 않았다. 각 축을 한 단계 낮췄다.
    for _ in range(30):
        day = pick_day(8)
        anomaly_at = _random_time(rng, day + timedelta(days=7), (2, 6))

        buyer = _new_user(
            next_user_id,
            # 정상 p5(18.7일) 근처 — 드물지만 평범한 범위의 신규 계정.
            anomaly_at - timedelta(days=float(rng.uniform(20, 45))),
        )
        users.append(buyer)
        next_user_id += 1
        seller_id = pick_existing()
        item_id = int(rng.choice(_HISTORY_ITEM_IDS))

        # 셋업 거래: 같은 상대와 7일 안에 2~3회(정상 p90~p95). 이 거래들
        # 자체는 정상과 구별되지 않으므로 라벨도 정상이다.
        for setup_index in range(int(rng.integers(2, 4))):
            setup_at = anomaly_at - timedelta(
                days=float(rng.uniform(0.5, 6.5)), hours=float(rng.uniform(0, 12))
            )
            if setup_at <= buyer["created_at"]:
                continue
            trades.append(
                _make_trade(
                    rng,
                    item_id,
                    buyer["user_id"],
                    seller_id,
                    setup_at,
                    float(rng.uniform(0.95, 1.05)),
                    None,
                )
            )

        trades.append(
            _make_trade(
                rng,
                item_id,
                buyer["user_id"],
                seller_id,
                anomaly_at,
                # 정상 p80~p90 구간(1.046~1.073배). 단독으로는 흔한 값이다.
                float(np.exp(rng.uniform(0.045, 0.070))),
                "subtle_composite",
            )
        )

    return users, trades
