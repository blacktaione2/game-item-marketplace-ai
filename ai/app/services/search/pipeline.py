"""검색 파이프라인 오케스트레이션.

자연어 질의 →
  (1) Query Rewrite + Text-to-DSL   [LLM 1회]
  (2) 임베딩
  (3) BM25 + kNN 하이브리드 검색     [msearch 1회] → RRF 융합
  (4) Cross-Encoder 리랭킹 (상위 N건만)
→ 최종 결과

LLM이 생성하는 자연어 설명은 이 단계에서 만들지 않는다(계획서상 별도 단계).
"""

from __future__ import annotations

import time
from typing import Any

from elasticsearch import AsyncElasticsearch

from app.core.config import get_settings
from app.core.threadpool import run_cpu
from app.services.llm.base import LLMClient
from app.services.search.embedding import get_embedding_service
from app.services.search.filters import QueryUnderstanding
from app.services.search.hybrid import hybrid_search
from app.services.search.mapping import index_name
from app.services.search.query_understanding import understand_query
from app.services.search.reranker import get_reranker


async def search(
    es: AsyncElasticsearch,
    llm_client: LLMClient,
    tenant_code: str,
    query: str,
    size: int = 10,
    use_rerank: bool = True,
) -> dict[str, Any]:
    settings = get_settings()
    timings: dict[str, float] = {}

    started = time.perf_counter()
    understanding: QueryUnderstanding = await understand_query(llm_client, query)
    timings["query_understanding_ms"] = _elapsed_ms(started)

    started = time.perf_counter()
    # 동기 CPU 호출이라 전용 스레드로 내보낸다 — 그냥 부르면 계산이 끝날 때까지
    # 이벤트 루프가 멈춘다(app/core/threadpool.py).
    vector = await run_cpu(get_embedding_service().encode_one, understanding.rewritten_query)
    timings["embedding_ms"] = _elapsed_ms(started)

    started = time.perf_counter()
    documents = await hybrid_search(
        es=es,
        index=index_name(settings.index_prefix, tenant_code),
        query_text=understanding.rewritten_query,
        query_vector=vector,
        filters=understanding.filters.to_es_filters(),
        candidate_size=settings.rerank_candidates,
    )
    timings["retrieval_ms"] = _elapsed_ms(started)

    if use_rerank and documents:
        started = time.perf_counter()
        # 이 경로에서 가장 큰 동기 블록이다(격리 median 103~189ms — 임베딩의 4~5배).
        documents = await run_cpu(
            get_reranker().rerank, understanding.rewritten_query, documents
        )
        timings["rerank_ms"] = _elapsed_ms(started)

    return {
        "query": query,
        "rewritten_query": understanding.rewritten_query,
        "filters": understanding.filters.model_dump(exclude_none=True),
        "reranked": use_rerank,
        "timings": timings,
        "results": documents[:size],
    }


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)
