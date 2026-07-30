"""인덱스 생성/문서 색인 유틸."""

from __future__ import annotations

from typing import Any

from elasticsearch import AsyncElasticsearch
from elasticsearch.helpers import async_bulk

from app.core.config import get_settings
from app.services.search.embedding import get_embedding_service
from app.services.search.mapping import build_index_body, index_name


async def ensure_index(
    es: AsyncElasticsearch,
    tenant_code: str,
    number_of_shards: int = 1,
    number_of_replicas: int = 0,
) -> str:
    settings = get_settings()
    name = index_name(settings.index_prefix, tenant_code)

    if not await es.indices.exists(index=name):
        await es.indices.create(
            index=name,
            body=build_index_body(
                embedding_dims=settings.embedding_dims,
                number_of_shards=number_of_shards,
                number_of_replicas=number_of_replicas,
            ),
        )
    return name


def embedding_text(item: dict[str, Any]) -> str:
    """임베딩 대상 텍스트. 이름과 설명을 합쳐 의미 벡터를 만든다."""
    return f"{item.get('name', '')} {item.get('description', '')}".strip()


async def index_items(
    es: AsyncElasticsearch, tenant_code: str, items: list[dict[str, Any]]
) -> int:
    name = await ensure_index(es, tenant_code)

    embedder = get_embedding_service()
    vectors = embedder.encode([embedding_text(item) for item in items])

    actions = [
        {
            "_index": name,
            "_id": str(item["item_id"]),
            "_source": {**item, "embedding": vector},
        }
        for item, vector in zip(items, vectors)
    ]

    succeeded, _ = await async_bulk(es, actions, refresh="wait_for")
    return succeeded
