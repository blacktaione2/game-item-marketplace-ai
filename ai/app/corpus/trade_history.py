"""아이템별 일별 거래 이력 (Phase 5 더미 데이터).

실제 시스템에서 이 자리는 백엔드 PostgreSQL의 trade 테이블을 일 단위로
집계한 결과가 들어올 곳이다. Phase 5에서는 아직 실거래가 없으므로 고정 시드
생성기로 대체한다.

**이 데이터는 패턴을 알고 만든 합성 데이터다.** 실측 시세가 아니므로 여기서
나온 예측 정확도를 그대로 "모델이 좋다"의 근거로 쓰면 안 된다. 그래서
학습 스크립트는 항상 나이브 기준선(직전값 유지 / 선형 외삽)과 같이 측정한다.

이력 보유 여부로 두 갈래를 모두 재현할 수 있게 구성했다:
- HISTORY_SPECS  : 120일 이력 보유 → 모델 직접 예측 경로
- COLD_START_SPECS: 이력 0~5일     → Cold Start 백오프 경로
- 두 dict 어디에도 없는 아이템은 "거래 이력 없음"으로 취급한다.
"""

from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache
from typing import Any

import numpy as np

from app.corpus import ALL_ITEMS

# 시리즈의 마지막 날(가장 최근 거래일). 오늘이 2026-07-29이므로 어제까지.
SERIES_END = date(2026, 7, 28)

_ITEMS_BY_ID = {item["item_id"]: item for item in ALL_ITEMS}

# 추세 종류. 변동성(volatility)은 추세와 분리된 축이라 별도 필드로 둔다 —
# 경매 아이템은 추세와 무관하게 변동이 크다.
TREND_KINDS = ("decline", "rise", "flat", "event_spike")

# 이력이 충분한 아이템. 카테고리마다 최소 1건씩 두어서, 콜드스타트가
# 어떤 카테고리에서 발생하든 상속받을 도너가 존재하게 했다.
HISTORY_SPECS: dict[int, dict[str, Any]] = {
    # 무기
    1: {"days": 120, "trend": "decline", "volatility": 0.025, "base_volume": 9},
    2: {"days": 120, "trend": "decline", "volatility": 0.020, "base_volume": 14},
    4: {"days": 120, "trend": "flat", "volatility": 0.060, "base_volume": 2},
    15: {"days": 120, "trend": "flat", "volatility": 0.025, "base_volume": 8},
    16: {"days": 120, "trend": "rise", "volatility": 0.040, "base_volume": 4},
    21: {"days": 120, "trend": "rise", "volatility": 0.030, "base_volume": 7},
    22: {"days": 120, "trend": "event_spike", "volatility": 0.030, "base_volume": 9},
    24: {"days": 120, "trend": "flat", "volatility": 0.050, "base_volume": 3},
    # 방어구
    6: {"days": 120, "trend": "flat", "volatility": 0.020, "base_volume": 7},
    7: {"days": 120, "trend": "rise", "volatility": 0.050, "base_volume": 3},
    17: {"days": 120, "trend": "decline", "volatility": 0.025, "base_volume": 8},
    20: {"days": 120, "trend": "flat", "volatility": 0.030, "base_volume": 6},
    # 장신구
    9: {"days": 120, "trend": "rise", "volatility": 0.030, "base_volume": 6},
    10: {"days": 120, "trend": "rise", "volatility": 0.050, "base_volume": 4},
    # 소모품
    11: {"days": 120, "trend": "flat", "volatility": 0.015, "base_volume": 40},
    12: {"days": 120, "trend": "event_spike", "volatility": 0.030, "base_volume": 25},
    19: {"days": 120, "trend": "rise", "volatility": 0.045, "base_volume": 5},
    # 계정 / 재화
    13: {"days": 120, "trend": "decline", "volatility": 0.060, "base_volume": 2},
    14: {"days": 120, "trend": "decline", "volatility": 0.020, "base_volume": 30},
}

# 이력이 부족한 아이템. days=0은 "등록만 되고 아직 한 건도 안 팔린" 상태로,
# 앵커 가격을 등록가에서 가져와야 하는 경로를 태우기 위한 케이스다.
COLD_START_SPECS: dict[int, dict[str, Any]] = {
    103: {"days": 0, "volatility": 0.0, "base_volume": 0},  # 그림자 암살검(무기)
    111: {"days": 3, "volatility": 0.02, "base_volume": 1},  # +9 은빛 사슬갑(방어구)
    114: {"days": 5, "volatility": 0.02, "base_volume": 1},  # 수호자의 팔찌(장신구)
    118: {"days": 2, "volatility": 0.03, "base_volume": 1},  # 레벨150 궁수 계정
}


def _trend_multiplier(kind: str, t: np.ndarray) -> np.ndarray:
    """0~1로 정규화된 시간축 t에 대한 추세 배수."""
    if kind == "decline":
        return 1.0 - 0.35 * t
    if kind == "rise":
        return 1.0 + 0.40 * t
    if kind == "flat":
        return np.ones_like(t)
    if kind == "event_spike":
        # 후반 70% 지점에 이벤트성 급등 후 회귀. 나이브 기준선이 못 따라가는
        # 구간이라 모델 비교에서 변별력이 생긴다.
        return 1.0 + 0.55 * np.exp(-(((t - 0.70) / 0.05) ** 2))
    raise ValueError(f"알 수 없는 추세 종류: {kind}")


def _ar1_noise(rng: np.random.Generator, days: int, volatility: float) -> np.ndarray:
    """자기상관 있는 노이즈.

    시세는 하루 튀었다가 다음 날 원위치하지 않으므로 iid 노이즈는 비현실적이다.
    반대로 순수 랜덤워크는 120일이면 발산해버린다. 평균회귀하는 AR(1)이
    두 극단 사이의 타협점.
    """
    phi = 0.7
    shocks = rng.normal(0.0, volatility, days)
    noise = np.empty(days)
    prev = 0.0
    for i in range(days):
        prev = phi * prev + shocks[i]
        noise[i] = prev
    return noise


def _generate(item_id: int, spec: dict[str, Any]) -> list[dict[str, Any]]:
    days = spec["days"]
    if days == 0:
        return []

    item = _ITEMS_BY_ID[item_id]
    base_price = float(item["price"])
    # 시드를 item_id로 고정 — 아이템을 추가해도 기존 아이템 시리즈는 안 바뀐다.
    rng = np.random.default_rng(item_id)

    t = np.linspace(0.0, 1.0, days) if days > 1 else np.zeros(1)
    trend = _trend_multiplier(spec.get("trend", "flat"), t)
    start_day = SERIES_END - timedelta(days=days - 1)
    weekday = np.array([(start_day + timedelta(days=i)).weekday() for i in range(days)])

    # 주간 주기: 주말에 거래가 몰리면서 가격도 소폭 오른다.
    weekly = 0.015 * np.sin(2 * np.pi * np.arange(days) / 7.0)
    log_price = (
        np.log(base_price)
        + np.log(trend)
        + weekly
        + _ar1_noise(rng, days, spec["volatility"])
    )
    prices = np.exp(log_price)

    weekend_factor = np.where(weekday >= 5, 1.6, 1.0)
    lam = np.maximum(spec["base_volume"] * weekend_factor * trend, 0.3)
    volumes = np.maximum(rng.poisson(lam), 1)

    return [
        {
            "date": (start_day + timedelta(days=i)).isoformat(),
            # 10원 단위로 반올림 — 실제 거래가도 딱 떨어지는 값으로 체결된다.
            "price": float(round(prices[i], -1)),
            "volume": int(volumes[i]),
        }
        for i in range(days)
    ]


@lru_cache
def _all_series() -> dict[int, list[dict[str, Any]]]:
    specs = {**HISTORY_SPECS, **COLD_START_SPECS}
    return {item_id: _generate(item_id, spec) for item_id, spec in specs.items()}


def get_price_series(item_id: int) -> list[dict[str, Any]]:
    """아이템의 일별 거래 이력(날짜 오름차순). 이력이 없으면 빈 리스트."""
    return _all_series().get(item_id, [])


def items_with_history(min_days: int) -> list[int]:
    """min_days 이상 이력을 가진 아이템 id 목록. 콜드스타트 도너 후보."""
    return sorted(
        item_id
        for item_id, series in _all_series().items()
        if len(series) >= min_days
    )
