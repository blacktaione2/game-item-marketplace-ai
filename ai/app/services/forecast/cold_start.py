"""Cold Start 백오프: 유사 아이템의 트렌드를 가중 상속.

거래가 거의 없는 아이템은 자기 이력만으로는 예측이 불가능하다. 그렇다고
"데이터 없음"만 반환하면 신규 등록 아이템이 전부 사각지대가 된다.

대신 Elasticsearch로 **같은 성격의 아이템군**을 찾아 그들의 최근 가격 궤적을
유사도 가중평균해서 물려받는다. dataset.py가 가격을 비율로 정규화해 두었기
때문에, 15만원짜리 도너의 궤적을 6만원짜리 신규 아이템에 그대로 씌울 수 있다
— 물려받는 것은 절대 가격이 아니라 "며칠 사이 몇 배가 되는가"의 모양이다.

앵커(기준 가격)는 상속하지 않는다. 그건 아이템 자신의 것(관측된 소수 거래의
평균, 없으면 등록가)을 쓴다. 모양은 남에게서, 수준은 자기에게서 가져오는 셈.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from elasticsearch import AsyncElasticsearch

from app.corpus.trade_history import get_price_series, items_with_history
from app.services.forecast.dataset import to_arrays
from app.services.search.embedding import get_embedding_service
from app.services.search.indexer import embedding_text


async def find_donors(
    es: AsyncElasticsearch,
    index: str,
    item: dict[str, Any],
    min_history_days: int,
    donor_count: int,
) -> list[dict[str, Any]]:
    """이력이 충분한 아이템 중 의미적으로 가장 가까운 것들을 찾는다."""
    pool = items_with_history(min_history_days)
    if not pool:
        return []

    vector = get_embedding_service().encode_one(embedding_text(item))
    category = item.get("category")

    # 1차: 같은 카테고리 안에서만. 무기 시세를 계정 시세로 설명하면 안 된다.
    hits = await _knn(es, index, vector, pool, donor_count, category)
    if not hits:
        # 해당 카테고리에 이력 보유 아이템이 없으면 카테고리 제약을 푼다.
        # 엉뚱한 카테고리를 물려받을 위험보다 예측 자체가 불가능한 쪽이 나쁘다.
        hits = await _knn(es, index, vector, pool, donor_count, None)

    return [
        {
            "item_id": hit["_source"]["item_id"],
            "name": hit["_source"]["name"],
            "category": hit["_source"]["category"],
            "similarity": float(hit["_score"]),
        }
        for hit in hits
    ]


async def _knn(
    es: AsyncElasticsearch,
    index: str,
    vector: list[float],
    pool: list[int],
    donor_count: int,
    category: str | None,
) -> list[dict[str, Any]]:
    filters: list[dict[str, Any]] = [{"terms": {"item_id": pool}}]
    if category:
        filters.append({"term": {"category": category}})

    response = await es.search(
        index=index,
        size=donor_count,
        knn={
            "field": "embedding",
            "query_vector": vector,
            "k": donor_count,
            "num_candidates": max(donor_count * 10, 50),
            "filter": filters,
        },
        source_excludes=["embedding"],
    )
    return response["hits"]["hits"]


def inherit_features(
    donors: list[dict[str, Any]], window: int
) -> tuple[np.ndarray, list[dict[str, Any]]]:
    """도너들의 최근 window일 궤적을 유사도 가중평균한 (window, 2) 피처.

    가중치를 붙인 도너 목록도 같이 돌려준다 — 어떤 아이템의 추세를 얼마나
    물려받았는지 보여줄 수 있어야 예측 결과를 설명할 수 있다.
    """
    usable = [d for d in donors if len(get_price_series(d["item_id"])) >= window]
    if not usable:
        return np.empty((0, 0), dtype=np.float32), []

    scores = np.array([d["similarity"] for d in usable], dtype=np.float32)
    weights = scores / scores.sum()

    stacked = np.zeros((window, 2), dtype=np.float32)
    for weight, donor in zip(weights, usable):
        prices, volumes = to_arrays(get_price_series(donor["item_id"])[-window:])
        # 도너마다 자기 마지막 가격 기준으로 정규화한 뒤 합쳐야 가격대가
        # 다른 도너들이 대등하게 섞인다.
        ratios = prices / float(prices[-1])
        volume_ratios = volumes / max(float(volumes.mean()), 1.0)
        stacked[:, 0] += weight * ratios
        stacked[:, 1] += weight * volume_ratios

    sources = [
        {**donor, "weight": round(float(weight), 4)}
        for donor, weight in zip(usable, weights)
    ]
    return stacked, sources


def anchor_price(item: dict[str, Any]) -> float:
    """예측의 기준 가격.

    관측된 거래가 있으면 그 평균(실제 체결가가 등록가보다 믿을 만하다),
    한 건도 없으면 등록가.
    """
    series = get_price_series(item["item_id"])
    if series:
        return float(np.mean([point["price"] for point in series]))
    return float(item["price"])
