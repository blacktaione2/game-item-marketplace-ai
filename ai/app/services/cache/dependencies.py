from functools import lru_cache

from app.core.config import get_settings
from app.services.cache.semantic_cache import SemanticCache


@lru_cache
def get_redis_client():
    # 임포트를 함수 안에 둔다 — 캐시를 끈 환경에서 redis 패키지를 강제하지
    # 않기 위해서다.
    from redis import asyncio as aioredis

    settings = get_settings()
    return aioredis.from_url(
        settings.redis_url, password=settings.redis_password or None
    )


@lru_cache
def get_semantic_cache() -> SemanticCache:
    settings = get_settings()
    return SemanticCache(
        redis_client=get_redis_client(),
        threshold=settings.semantic_cache_threshold,
        max_entries=settings.semantic_cache_max_entries,
        version=settings.cache_version,
    )
