"""BM25 + kNN 하이브리드 검색 및 RRF(Reciprocal Rank Fusion) 융합.

RRF를 Elasticsearch의 `rank.rrf`로 처리하지 않고 애플리케이션에서 직접
계산한다 — ES의 네이티브 RRF는 Platinum/Enterprise 라이선스 전용이고 이
프로젝트는 basic 라이선스이기 때문이다(ADR-0005 참고).

BM25와 kNN은 `_msearch`로 한 번의 왕복에 같이 던진다.
"""

from __future__ import annotations

from typing import Any

from elasticsearch import AsyncElasticsearch

from app.services.search.exceptions import TenantIndexNotFoundError

# RRF 표준 상수. 순위가 낮은 문서의 기여도를 완만하게 떨어뜨리는 역할.
RRF_RANK_CONSTANT = 60


def _bm25_body(query: str, filters: list[dict[str, Any]], size: int) -> dict[str, Any]:
    return {
        "size": size,
        "query": {
            "bool": {
                # name에 가중치를 더 줌 — 아이템명 일치가 설명문 일치보다 중요.
                "must": [
                    {
                        "multi_match": {
                            "query": query,
                            "fields": ["name^3", "description"],
                        }
                    }
                ],
                "filter": filters,
            }
        },
        "_source": {"excludes": ["embedding"]},
    }


def _knn_body(
    vector: list[float], filters: list[dict[str, Any]], size: int
) -> dict[str, Any]:
    knn: dict[str, Any] = {
        "field": "embedding",
        "query_vector": vector,
        "k": size,
        # num_candidates를 k보다 크게 잡아야 근사 kNN 리콜이 확보된다.
        "num_candidates": max(size * 5, 50),
    }
    if filters:
        knn["filter"] = filters
    return {"size": size, "knn": knn, "_source": {"excludes": ["embedding"]}}


def reciprocal_rank_fusion(
    ranked_lists: list[list[str]], rank_constant: int = RRF_RANK_CONSTANT
) -> list[tuple[str, float]]:
    """여러 랭킹 리스트를 RRF로 융합해 (doc_id, score) 내림차순 리스트를 반환.

    score(d) = sum over lists of 1 / (rank_constant + rank(d))
    rank는 1부터 시작한다. 점수 스케일이 완전히 다른 BM25와 코사인 유사도를
    직접 더하지 않고 순위만 쓰기 때문에 정규화가 필요 없다는 게 RRF의 요점.
    """
    scores: dict[str, float] = {}
    for ranked in ranked_lists:
        for rank, doc_id in enumerate(ranked, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (rank_constant + rank)
    return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)


async def hybrid_search(
    es: AsyncElasticsearch,
    index: str,
    query_text: str,
    query_vector: list[float],
    filters: list[dict[str, Any]],
    candidate_size: int,
) -> list[dict[str, Any]]:
    """BM25와 kNN을 한 번의 msearch로 실행하고 RRF로 융합한 문서 리스트를 반환."""
    body: list[dict[str, Any]] = [
        {"index": index},
        _bm25_body(query_text, filters, candidate_size),
        {"index": index},
        _knn_body(query_vector, filters, candidate_size),
    ]

    result = await es.msearch(searches=body)
    responses = result["responses"]

    for response in responses:
        error = response.get("error")
        if error:
            if error.get("type") == "index_not_found_exception":
                raise TenantIndexNotFoundError(index)
            raise RuntimeError(f"Elasticsearch 검색 실패: {error}")

    bm25_hits = responses[0]["hits"]["hits"]
    knn_hits = responses[1]["hits"]["hits"]

    documents: dict[str, dict[str, Any]] = {}
    for hit in [*bm25_hits, *knn_hits]:
        documents[hit["_id"]] = hit["_source"]

    fused = reciprocal_rank_fusion(
        [[hit["_id"] for hit in bm25_hits], [hit["_id"] for hit in knn_hits]]
    )

    bm25_ranks = {hit["_id"]: i + 1 for i, hit in enumerate(bm25_hits)}
    knn_ranks = {hit["_id"]: i + 1 for i, hit in enumerate(knn_hits)}

    return [
        {
            **documents[doc_id],
            "_id": doc_id,
            "rrf_score": score,
            # 어느 검색기가 이 문서를 올렸는지 남겨두면 튜닝할 때 판단 근거가 된다.
            "bm25_rank": bm25_ranks.get(doc_id),
            "knn_rank": knn_ranks.get(doc_id),
        }
        for doc_id, score in fused
    ]
