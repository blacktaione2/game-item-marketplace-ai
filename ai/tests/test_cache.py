"""시맨틱 캐시 키 규칙과 의도별 정책 테스트.

Redis 연결 없이 검증 가능한 부분만 다룬다. 임계값의 적절성은 단위 테스트가
아니라 `scripts.evaluate_semantic_cache`의 오탐율/적중률 측정으로 판단한다.
"""

from datetime import datetime

import pytest

from app.services.cache.policy import allows_semantic, is_cacheable, ttl_seconds
from app.services.cache.semantic_cache import SemanticCache
from app.services.router.intents import Intent


@pytest.fixture
def cache():
    return SemanticCache(
        redis_client=None, threshold=0.98, max_entries=100, version="v1"
    )


class TestTenantIsolation:
    """테넌트가 섞이면 성능이 아니라 **정확성** 문제다 — 남의 답이 나간다."""

    def test_same_query_different_tenants_get_different_keys(self, cache):
        assert cache.entry_key("nexon", "롱소드 시세") != cache.entry_key(
            "ncsoft", "롱소드 시세"
        )

    def test_index_keys_are_per_tenant(self, cache):
        assert cache.index_key("nexon") != cache.index_key("ncsoft")

    def test_tenant_code_appears_in_key(self, cache):
        assert "nexon" in cache.entry_key("nexon", "롱소드 시세")


class TestVersionSegment:
    """버전만 올리면 통째로 무효화된다 — 재색인/재학습 시 쓰는 장치."""

    def test_version_change_invalidates_keys(self):
        v1 = SemanticCache(None, 0.98, 100, "v1")
        v2 = SemanticCache(None, 0.98, 100, "v2")
        assert v1.entry_key("nexon", "롱소드") != v2.entry_key("nexon", "롱소드")


class TestEntryKey:
    def test_same_query_is_stable(self, cache):
        assert cache.entry_key("nexon", "롱소드") == cache.entry_key("nexon", "롱소드")

    def test_whitespace_is_normalized(self, cache):
        assert cache.entry_key("nexon", "  롱소드  ") == cache.entry_key(
            "nexon", "롱소드"
        )

    def test_different_queries_differ(self, cache):
        assert cache.entry_key("nexon", "+8 롱소드") != cache.entry_key(
            "nexon", "+9 롱소드"
        )

    def test_trailing_punctuation_does_not_make_a_new_key(self, cache):
        """`"...알려줘!"` 와 `"...알려줘"` 는 같은 질문이다.

        실사용에서 걸린 것 — 느낌표 하나로 미적중이 났다. **유사도를 연 게 아니다.**
        여전히 정확 일치이고, "같은 글자"의 범위를 종결 기호만큼 넓혔을 뿐이다.
        """
        base = cache.entry_key("nexon", "불꽃의 대검 시세 알려줘")
        for variant in ("불꽃의 대검 시세 알려줘!", "불꽃의 대검 시세 알려줘?",
                        "불꽃의 대검 시세 알려줘.", "불꽃의 대검  시세   알려줘 "):
            assert cache.entry_key("nexon", variant) == base, variant

    def test_punctuation_only_queries_stay_distinct(self, cache):
        """정규화가 질의를 통째로 비우면 안 된다.

        `"???"` 와 `"!!!"` 는 둘 다 무의미하지만 **같은 답을 줄 이유는 없다.**
        rstrip 결과가 비면 원본으로 되돌린다.
        """
        assert cache.entry_key("nexon", "???") != cache.entry_key("nexon", "!!!")

    def test_trap_pairs_still_get_different_keys(self, cache):
        """**이쪽이 진짜 단언이다.** 정규화가 함정 쌍을 삼키면 사고다.

        ADR-0012 가 유사도 매칭을 FAQ 로 제한한 근거가 이 쌍들이다 — 한 글자가
        답을 뒤집는데 임베딩은 0.9787 까지 붙는다. 문장부호 정규화는 그 축을
        건드리지 않아야 하고, 그걸 여기서 코퍼스 전체로 확인한다.
        """
        from app.corpus.cache_pairs import TRAP_PAIRS_HOLDOUT, TRAP_PAIRS_TUNING

        for left, right in TRAP_PAIRS_TUNING + TRAP_PAIRS_HOLDOUT:
            assert cache.entry_key("nexon", left) != cache.entry_key(
                "nexon", right
            ), f"{left!r} 과 {right!r} 이 같은 키가 됐다"


class TestPolicy:
    def test_anomaly_check_is_never_cached(self):
        """캐시 오탐이 곧 보안 오판이 되는 유일한 의도다."""
        assert is_cacheable(Intent.ANOMALY_CHECK) is False
        assert ttl_seconds(Intent.ANOMALY_CHECK) == 0

    def test_faq_lives_longest(self):
        assert ttl_seconds(Intent.FAQ_SMALLTALK) > ttl_seconds(Intent.ITEM_SEARCH)

    def test_forecast_expires_at_midnight(self):
        """예측은 일별 시세 기준이라 날짜가 바뀌면 근거가 바뀐다."""
        near_midnight = datetime(2026, 7, 29, 23, 30, 0)
        assert ttl_seconds(Intent.PRICE_FORECAST, near_midnight) == 30 * 60

    def test_semantic_matching_only_where_a_wrong_hit_is_cheap(self):
        """실측상 질의-질의 유사도로는 함정 쌍이 안 걸러진다 — ADR-0012."""
        assert allows_semantic(Intent.FAQ_SMALLTALK) is True
        assert allows_semantic(Intent.PRICE_FORECAST) is False


class TestNoResultResponsesAreNotStored:
    """0건 응답은 의도가 캐시 가능이어도 저장하지 않는다 — ADR-0016.

    0건 판정의 근거인 필터 추출이 비결정적이라, 질의 문자열로 캐시하면 여러
    추출 중 하나를 TTL 동안 임의로 고정하게 된다. 그리고 0건은 매물 하나가
    등록되면 즉시 거짓이 되는 가장 낡기 쉬운 답이다.
    """

    def test_search_is_cacheable_by_intent(self):
        assert is_cacheable(Intent.ITEM_SEARCH) is True

    def test_but_not_when_the_response_found_nothing(self):
        assert is_cacheable(Intent.ITEM_SEARCH, {"no_results": True}) is False

    def test_normal_search_response_is_still_stored(self):
        response = {"results": [{"item_id": 1}], "answer": "..."}
        assert is_cacheable(Intent.ITEM_SEARCH, response) is True

    def test_uncacheable_intent_stays_uncacheable_with_a_response(self):
        """응답 인자가 의도 정책을 뒤집지는 않는다."""
        assert is_cacheable(Intent.ANOMALY_CHECK, {"detection": {}}) is False
        assert allows_semantic(Intent.ITEM_SEARCH) is False
        assert allows_semantic(Intent.ANOMALY_CHECK) is False


# --- 적중 경로에서 임베딩을 부르지 않는다 (ADR-0026) -------------------------


class _FakeRedis:
    """`lookup()`이 쓰는 만큼만 흉내낸다."""

    def __init__(self, entries: dict[str, str]):
        self._entries = entries

    async def smembers(self, key: str):
        return {k.encode() for k in self._entries}

    async def mget(self, keys: list[str]):
        return [self._entries.get(k) for k in keys]

    async def srem(self, key: str, *members):
        return 0


def _stored_payload(query: str, vector: list[float], intent: str) -> str:
    import base64
    import json

    import numpy as np

    return json.dumps(
        {
            "query": query,
            "embedding": base64.b64encode(
                np.asarray(vector, dtype=np.float32).tobytes()
            ).decode("ascii"),
            "response": {"answer": "캐시된 응답"},
            "intent": intent,
        },
        ensure_ascii=False,
    )


class TestEmbeddingIsNotComputedOnExactHit:
    """정확 일치 적중에서 **임베딩 호출 자체가 일어나지 않아야** 한다.

    적중률이나 `llm_calls`가 그대로인 것만으로는 확인되지 않는다 — 결과가 같아도
    내부에서 여전히 즉시 계산하고 있을 수 있기 때문이다. 그래서 **부르면 터지는**
    콜러블을 넘긴다.

    이게 중요한 이유는 절약되는 15.77ms가 자기 요청에서 끝나지 않기 때문이다.
    `encode_one`은 동기 CPU 호출이라 `async` 핸들러에서 **이벤트 루프를 막고**,
    동시 부하에서는 그 시간이 다른 요청의 대기로 번진다(ADR-0026).
    """

    def _cache(self, entries):
        return SemanticCache(
            redis_client=_FakeRedis(entries), threshold=0.98, max_entries=100, version="v1"
        )

    def test_exact_hit_never_calls_embed(self):
        import asyncio

        # **일부러 동기 함수로 둔다.** 콜러블은 async로 바뀌었지만(ADR-0028),
        # `async def`로 쓰면 호출만으로는 코루틴이 생길 뿐 아무 일도 안 일어나고
        # `await` 해야 터진다 — 즉 `embed()`를 부르고 기다리지 않는 구현을
        # 통과시킨다. 동기 함수는 **호출되는 순간** 터지므로 가드가 더 강하다.
        def explode():
            raise AssertionError("정확 일치인데 임베딩이 계산됐다")

        cache = SemanticCache(
            redis_client=None, threshold=0.98, max_entries=100, version="v1"
        )
        key = cache.entry_key("nexon", "롱소드 시세")
        cache = self._cache({key: _stored_payload("롱소드 시세", [0.1] * 384, "item_search")})

        hit = asyncio.run(cache.lookup("nexon", "롱소드 시세", explode))
        assert hit is not None
        assert hit["match_type"] == "exact"

    def test_miss_calls_embed_exactly_once(self):
        """유사도 경로는 임베딩이 필요하다 — 안 부르면 그것대로 결함이다."""
        import asyncio

        calls = []

        async def embed():
            calls.append(1)
            return [0.0] * 384

        cache = SemanticCache(
            redis_client=None, threshold=0.98, max_entries=100, version="v1"
        )
        key = cache.entry_key("nexon", "다른 질의")
        cache = self._cache({key: _stored_payload("다른 질의", [0.1] * 384, "faq_smalltalk")})

        asyncio.run(cache.lookup("nexon", "롱소드 시세", embed))
        assert len(calls) == 1
