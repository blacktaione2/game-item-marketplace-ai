from functools import lru_cache

from elasticsearch import AsyncElasticsearch

from app.core.config import get_settings


@lru_cache
def get_es_client() -> AsyncElasticsearch:
    return AsyncElasticsearch(get_settings().elasticsearch_url)
