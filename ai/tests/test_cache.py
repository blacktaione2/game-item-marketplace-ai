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
