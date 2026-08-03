"""요청 한도 테스트 (ADR-0024).

Redis 없이 가짜 클라이언트로 돈다. 여기서 재는 것은 **연결이 되는가**가 아니라
카운팅 규칙이 맞는가다 — 특히 `EXPIRE`를 창의 첫 요청에만 거는지.

그 규칙이 틀리면 증상이 조용하다. 매 요청마다 만료를 갱신하면 창이 계속 밀려서
**한도가 사실상 사라지는데**, 정상 응답만 나오므로 아무도 눈치채지 못한다.
고정 윈도우 구현에서 가장 흔한 결함이라 회귀로 박아둔다.
"""

import asyncio

import pytest
from fastapi import HTTPException

from app.core import rate_limit
from app.core.auth import Actor
from app.core.config import get_settings


class FakeRedis:
    """`INCR` / `EXPIRE` / `TTL` 만 흉내낸다."""

    def __init__(self):
        self.counts: dict[str, int] = {}
        self.ttls: dict[str, int] = {}
        self.expire_calls: list[str] = []

    async def incr(self, key: str) -> int:
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key: str, seconds: int) -> bool:
        self.expire_calls.append(key)
        self.ttls[key] = seconds
        return True

    async def ttl(self, key: str) -> int:
        return self.ttls.get(key, -1)


class BrokenRedis:
    async def incr(self, key: str) -> int:
        raise ConnectionError("redis 없음")


@pytest.fixture
def fake_redis(monkeypatch):
    redis = FakeRedis()
    monkeypatch.setattr(rate_limit, "get_redis_client", lambda: redis)
    get_settings.cache_clear()
    yield redis
    get_settings.cache_clear()


def actor(user_id=3, tenant="nexon"):
    return Actor(user_id=user_id, tenant_id=1, tenant_code=tenant, role="USER")


def call(who):
    return asyncio.run(rate_limit.limit_assistant(who))


def test_under_the_limit_passes(fake_redis):
    limit = get_settings().rate_limit_assistant_per_min
    for _ in range(limit):
        assert call(actor()) is not None


def test_one_over_the_limit_is_rejected(fake_redis):
    limit = get_settings().rate_limit_assistant_per_min
    for _ in range(limit):
        call(actor())
    with pytest.raises(HTTPException) as caught:
        call(actor())
    assert caught.value.status_code == 429
    assert "Retry-After" in caught.value.headers


def test_expire_is_set_only_once_per_window(fake_redis):
    """매 요청마다 만료를 갱신하면 창이 밀려 한도가 사라진다.

    **창이 둘이라 2건이 맞다** — 분당과 일당(ADR-0031). 각 창의 첫 요청에서
    한 번씩 걸리고, 이후 4번의 호출에서는 더 걸리지 않는다. 규칙 자체는 그대로다.
    """
    for _ in range(5):
        call(actor())
    assert len(fake_redis.expire_calls) == 2
    assert len(set(fake_redis.expire_calls)) == 2  # 서로 다른 키

    minute_keys = [k for k in fake_redis.expire_calls if ":assistant:" in k]
    daily_keys = [k for k in fake_redis.expire_calls if ":assistant_daily:" in k]
    assert len(minute_keys) == 1
    assert len(daily_keys) == 1


def test_daily_key_carries_the_kst_date(fake_redis):
    """일일 창은 **키에 날짜가 들어가** 한국시간 자정에 리셋된다 (ADR-0031).

    Redis 만료에 맡기면 창이 그 사용자의 첫 호출 기준이라 사람마다 리셋 시각이
    다르고 예측이 안 된다. 날짜가 키에 있으면 "한국시간 자정"이라고 설명할 수 있다.
    """
    from datetime import datetime, timedelta, timezone

    call(actor())
    expected = (datetime.now(timezone.utc) + timedelta(hours=9)).strftime("%Y-%m-%d")
    daily = [k for k in fake_redis.counts if ":assistant_daily:" in k]
    assert len(daily) == 1
    assert expected in daily[0]


def test_daily_limit_rejects_beyond_its_cap(fake_redis, monkeypatch):
    """분당 한도를 안 넘겨도 **일일 한도**에 걸린다.

    분당 한도를 크게 올려 그쪽이 개입하지 않게 한 뒤, 일일 한도만 넘긴다.
    """
    settings = get_settings()
    monkeypatch.setattr(settings, "rate_limit_assistant_per_min", 100_000)
    monkeypatch.setattr(settings, "rate_limit_assistant_per_day", 3)

    for _ in range(3):
        assert call(actor()) is not None

    with pytest.raises(HTTPException) as exc:
        call(actor())
    assert exc.value.status_code == 429


def test_minute_rejection_does_not_consume_daily_budget(fake_redis, monkeypatch):
    """분당에 막힌 요청이 **일일 한도를 갉아먹으면 안 된다.**

    먼저 올리고 나중에 검사하면 그렇게 된다 — 사용자가 잠깐 몰아친 대가로
    하루치를 잃는다.
    """
    settings = get_settings()
    monkeypatch.setattr(settings, "rate_limit_assistant_per_min", 2)
    monkeypatch.setattr(settings, "rate_limit_assistant_per_day", 50)

    for _ in range(6):
        try:
            call(actor())
        except HTTPException:
            pass

    daily_key = next(k for k in fake_redis.counts if ":assistant_daily:" in k)
    # 6번 시도했지만 분당을 통과한 것은 2번뿐이다.
    assert fake_redis.counts[daily_key] == 2


def test_users_do_not_share_a_bucket(fake_redis):
    """한 사용자가 다 써도 다른 사용자는 통과해야 한다.

    이게 깨지면 전역 한도라서 한 명이 전체를 마비시킬 수 있다.
    """
    limit = get_settings().rate_limit_assistant_per_min
    for _ in range(limit + 1):
        try:
            call(actor(user_id=3))
        except HTTPException:
            pass
    assert call(actor(user_id=4)) is not None


def test_tenants_do_not_share_a_bucket(fake_redis):
    """사용자 id는 테넌트별로 갈리므로 id만 쓰면 남의 한도를 잡아먹는다."""
    limit = get_settings().rate_limit_assistant_per_min
    for _ in range(limit + 1):
        try:
            call(actor(user_id=3, tenant="nexon"))
        except HTTPException:
            pass
    assert call(actor(user_id=3, tenant="ncsoft")) is not None


def test_key_carries_the_configured_limit(fake_redis):
    """한도를 바꾸면 키가 바뀌어야 옛 카운터가 따라오지 않는다."""
    call(actor())
    key = next(iter(fake_redis.counts))
    limit = get_settings().rate_limit_assistant_per_min
    assert f"{limit}/60s" in key
    assert "nexon" in key


def test_disabled_setting_bypasses_entirely(fake_redis, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    get_settings.cache_clear()
    for _ in range(200):
        call(actor())
    assert fake_redis.counts == {}  # Redis를 아예 안 건드린다


def test_redis_failure_fails_open(monkeypatch):
    """리미터는 비용 방어이지 정합성 장치가 아니다.

    Redis가 죽었다고 서비스 전체를 막는 쪽이 더 나쁘다. 구매 경로의 분산 락은
    반대다 — 거긴 정합성이라 실패하면 거절한다.
    """
    monkeypatch.setattr(rate_limit, "get_redis_client", lambda: BrokenRedis())
    get_settings.cache_clear()
    assert call(actor()) is not None
