"""시세 예측 오케스트레이션.

아이템 →
  (1) 거래 이력 조회
  (2) 이력 충분?  → 자기 이력 윈도우 그대로 사용
      이력 부족?  → Cold Start 백오프: ES 유사 아이템 트렌드 가중 상속
  (3) LSTM 추론 (정규화 비율 → 앵커 가격 곱해 환산)
→ 일자별 예측가

두 경로 모두 같은 모델·같은 피처 형태를 쓴다. 콜드스타트가 바꾸는 것은
"윈도우를 어디서 구했는가"뿐이라, 분기가 예측 로직을 복제하지 않는다.
"""

from __future__ import annotations

import time
from datetime import date, timedelta
from typing import Any

from elasticsearch import AsyncElasticsearch
from elasticsearch import NotFoundError as EsNotFoundError

from app.corpus.trade_history import SERIES_END, get_price_series
from app.core.config import get_settings
from app.services.forecast.cold_start import anchor_price, find_donors, inherit_features
from app.services.forecast.dataset import normalize_window, to_arrays
from app.services.forecast.exceptions import (
    HorizonTooLongError,
    InsufficientHistoryError,
    ItemNotFoundError,
)
from app.services.forecast.predictor import get_forecast_service
from app.services.search.exceptions import TenantIndexNotFoundError
from app.services.search.mapping import index_name

# 응답에 같이 실어 보낼 과거 구간 길이(일). 그래프에서 추세가 보일 만큼이면
# 충분하고, 길어질수록 페이로드만 커진다.
HISTORY_POINTS = 30


async def forecast_price(
    es: AsyncElasticsearch,
    tenant_code: str,
    item_id: int,
    horizon: int | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    service = get_forecast_service()
    window = service.window
    horizon = horizon or service.horizon
    if horizon > service.horizon:
        raise HorizonTooLongError(horizon, service.horizon)

    index = index_name(settings.index_prefix, tenant_code)
    item = await _get_item(es, index, item_id)

    timings: dict[str, float] = {}
    series = get_price_series(item_id)
    cold_start = len(series) < settings.forecast_min_history_days

    started = time.perf_counter()
    if cold_start:
        donors = await find_donors(
            es=es,
            index=index,
            item=item,
            min_history_days=settings.forecast_min_history_days,
            donor_count=settings.forecast_donor_count,
        )
        features, sources = inherit_features(donors, window)
        if not sources:
            raise InsufficientHistoryError(item_id)
        anchor = anchor_price(item)
        last_date = SERIES_END
    else:
        prices, volumes = to_arrays(series[-window:])
        features = normalize_window(prices, volumes)
        anchor = float(prices[-1])
        sources = []
        last_date = date.fromisoformat(series[-1]["date"])
    timings["window_ms"] = _elapsed_ms(started)

    started = time.perf_counter()
    ratios = service.predict_ratios(features)[:horizon]
    timings["inference_ms"] = _elapsed_ms(started)

    forecast = [
        {
            "date": (last_date + timedelta(days=step)).isoformat(),
            "price": float(round(anchor * float(ratio), -1)),
            "ratio": round(float(ratio), 4),
        }
        for step, ratio in enumerate(ratios, start=1)
    ]

    return {
        "item_id": item_id,
        "name": item["name"],
        "category": item["category"],
        "cold_start": cold_start,
        "history_days": len(series),
        # 최근 실적. 예측 7일치만 주면 그래프에 점이 7개뿐이라 추세를 볼 수
        # 없어서 화면용으로 같이 내보낸다. 콜드스타트 아이템은 여기가 비거나
        # 아주 짧은데, 그 자체가 "왜 추정치인지"를 보여주는 신호다.
        "history": [
            {"date": point["date"], "price": point["price"]}
            for point in series[-HISTORY_POINTS:]
        ],
        "anchor_price": round(anchor, 1),
        "horizon_days": horizon,
        "forecast": forecast,
        "expected_change_pct": round((float(ratios[-1]) - 1.0) * 100.0, 2),
        # 콜드스타트일 때만 채워진다 — 어느 아이템 추세를 얼마나 물려받았는지.
        "inherited_from": sources,
        "timings": timings,
    }


async def _get_item(
    es: AsyncElasticsearch, index: str, item_id: int
) -> dict[str, Any]:
    try:
        response = await es.get(
            index=index, id=str(item_id), source_excludes=["embedding"]
        )
    except EsNotFoundError as e:
        # 인덱스가 없는 것과 문서가 없는 것은 원인이 완전히 달라서 구분해준다.
        if not await es.indices.exists(index=index):
            raise TenantIndexNotFoundError(index) from e
        raise ItemNotFoundError(item_id) from e
    return response["_source"]


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)
