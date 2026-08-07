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

import asyncio
import time
from typing import Any

from elasticsearch import AsyncElasticsearch

from app.core.config import get_settings
from app.core.threadpool import run_cpu
from app.services.llm.base import LLMClient
from app.services.search.domain_gate import judge_in_domain
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

    # **질의이해와 도메인 판정을 동시에 던진다** (ADR-0039). 순차로 하면 왕복이
    # 하나 더 붙지만, 둘 다 순수 I/O 대기라 같이 던지면 느린 쪽 하나만큼만 걸린다.
    #
    # 판정을 먼저 하고 통과할 때만 이해시키면 도메인 밖 요청의 호출을 하나 아낄
    # 수 있다. 그렇게 하지 않은 이유: **아끼는 쪽은 드물고(정상 트래픽은 거의 다
    # 도메인 안이다) 대가는 모든 요청의 지연**이기 때문이다.
    #
    # 두 시간을 따로 잰다. `gather` 로 묶어놓고 합쳐서 재면 "질의이해가 느려졌다"와
    # "판정이 느려졌다"를 구분할 수 없다.
    async def _timed(awaitable: Any, key: str) -> Any:
        started = time.perf_counter()
        result = await awaitable
        timings[key] = _elapsed_ms(started)
        return result

    understanding: QueryUnderstanding
    in_domain: bool
    understanding, in_domain = await asyncio.gather(
        _timed(understand_query(llm_client, query), "query_understanding_ms"),
        _timed(judge_in_domain(llm_client, query), "domain_gate_ms"),
    )

    if not in_domain:
        # **도메인 밖이면 검색 자체를 하지 않는다** (ADR-0039). 임베딩·ES·리랭커를
        # 태워봐야 kNN 은 어떤 질의에도 k 건을 돌려주므로 결과는 무의미하고,
        # 그 무의미한 결과가 상류에서 "찾았다"로 읽히는 게 이 결함의 경로였다.
        #
        # 판정은 여기서 하지 않는다 — 사실만 실어 보내고 무엇을 할지는 호출자가
        # 정한다. `no_results` 와 같은 역할 분담이다: 파이프라인은 결과를 내고
        # 정책은 `assistant/pipeline.py` 가 갖는다.
        return {
            "query": query,
            "rewritten_query": understanding.rewritten_query,
            "filters": understanding.filters.model_dump(exclude_none=True),
            "in_domain": False,
            "reranked": False,
            "timings": timings,
            "results": [],
        }

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
        "in_domain": True,
        "reranked": use_rerank,
        "timings": timings,
        "results": documents[:size],
    }


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)
