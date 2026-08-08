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

    async def get(self, key: str):
        # 실제 Redis 는 문자열(바이트)을 준다. int 를 주면 `_peek` 의
        # `int(raw)` 가 우연히 통과해서, **형 변환이 틀려도 안 잡힌다.**
        value = self.counts.get(key)
        return None if value is None else str(value)


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
    """의존성만 — 요청 **앞**에서 도는 부분이다."""
    return asyncio.run(rate_limit.limit_assistant(who))


def request(who, cached=False):
    """요청 하나를 통째로 흉내낸다 — 검사(앞) + 일일 정산(뒤).

    **ADR-0044 이후 일일 카운터는 의존성이 올리지 않는다.** 응답을 보고
    `consume_daily()` 가 올리고, 캐시 적중이면 올리지 않는다. 그래서 한도 동작을
    재려면 두 쪽을 다 밟아야 한다 — 의존성만 부르면 일일 한도가 영원히 0이다.
    """
    actor_out = rate_limit.limit_assistant(who)
    result = asyncio.run(actor_out)
    if not cached:
        asyncio.run(rate_limit.consume_daily(who))
    return result


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

    ADR-0044 이후 두 창을 **서로 다른 시점**이 만든다 — 분당은 의존성이(요청 앞),
    일당은 `consume_daily()` 가(응답 뒤). 그래서 `request()` 로 밟는다.
    """
    for _ in range(5):
        request(actor())
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

    request(actor())
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
        assert request(actor()) is not None

    with pytest.raises(HTTPException) as exc:
        request(actor())
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
            request(actor())
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


class TestDailyExpiryTracksMidnight:
    """일일 키의 TTL 이 **자정까지**여야 한다 (2026-08-07).

    리셋 시각은 키 이름의 날짜가 정하므로 TTL 은 청소용이다 — 그런데 그 값이
    `Retry-After` 로 사용자에게 나간다. 86,400(첫 호출 + 24시간)을 걸면
    "언제 풀리는가"를 최대 하루 가까이 과장해서 답하게 된다.
    """

    def test_returns_time_to_midnight_not_a_full_day(self):
        from datetime import datetime

        # 01:00 에 첫 호출 -> 자정까지 23시간. 24시간이 아니다.
        at_0100 = datetime(2026, 8, 7, 1, 0, 0)
        assert rate_limit.seconds_until_kst_midnight(at_0100) == 23 * 3600

        # 23:00 이면 1시간. 예전 판본은 여기서도 79,200(22시간)을 냈다.
        at_2300 = datetime(2026, 8, 7, 23, 0, 0)
        assert rate_limit.seconds_until_kst_midnight(at_2300) == 3600

    def test_never_returns_zero(self):
        """자정 정각에 0을 주면 `EXPIRE key 0` 이 되어 키가 즉시 사라진다."""
        from datetime import datetime

        just_before = datetime(2026, 8, 7, 23, 59, 59, 999999)
        assert rate_limit.seconds_until_kst_midnight(just_before) >= 1

    def test_the_limiter_uses_it_for_the_daily_key(self, fake_redis, monkeypatch):
        """함수만 맞고 배선이 안 되면 아무것도 안 고친 것이다.

        **실제 남은 초로 단언하지 않는다** — 실행 시각에 따라 달라져서 범위로
        볼 수밖에 없고, `1 <= x <= 86400` 은 예전 값(86,400)도 통과시킨다.
        함수를 가짜 값으로 바꿔치고 **그 값이 그대로 TTL 이 되는지**를 본다.

        분당 키가 60인 것도 같이 본다 — 안 그러면 "전부 자정"으로 바꿔놓고도
        통과한다.
        """
        monkeypatch.setattr(rate_limit, "seconds_until_kst_midnight", lambda _: 4321)
        # 일일 TTL 은 이제 `consume_daily()` 가 건다(ADR-0044). 의존성만 부르면
        # 일일 키 자체가 안 생긴다.
        request(actor())

        daily = [k for k in fake_redis.ttls if "assistant_daily" in k]
        minute = [k for k in fake_redis.ttls if "assistant_daily" not in k]
        assert len(daily) == 1 and len(minute) == 1

        assert fake_redis.ttls[daily[0]] == 4321
        assert fake_redis.ttls[minute[0]] == 60


class TestCacheHitsDoNotSpendTheDailyBudget:
    """일일 한도는 **LLM 비용**을 막는 계층이다 (ADR-0044).

    캐시 적중은 LLM 을 0회 부르고 p95 25.9ms 다. 그걸 예산에서 깎으면 리미터가
    자기가 막겠다고 한 것과 다른 것을 세게 된다.

    **이론이 아니라 실제로 닿던 경로다.** 프론트가 배지를 정직하게 유지하려고
    `staleTime` 을 안 걸어서(ADR-0037), 아이템 상세에 들어갔다 돌아올 때마다
    재요청이 나간다 — 서버는 캐시로 답하는데 하루치는 깎였다.
    """

    def test_적중은_예산을_안_쓴다(self, fake_redis, monkeypatch):
        settings = get_settings()
        monkeypatch.setattr(settings, "rate_limit_assistant_per_min", 100_000)
        monkeypatch.setattr(settings, "rate_limit_assistant_per_day", 3)

        for _ in range(20):
            assert request(actor(), cached=True) is not None

        daily = [k for k in fake_redis.counts if ":assistant_daily:" in k]
        assert daily == [], "캐시 적중이 일일 카운터를 만들었다"

    def test_미적중은_예산을_쓴다(self, fake_redis, monkeypatch):
        # **대조군이다.** 위 검사만 있으면 "일일 카운터가 아예 안 돈다"와
        # 구분되지 않는다.
        settings = get_settings()
        monkeypatch.setattr(settings, "rate_limit_assistant_per_min", 100_000)
        monkeypatch.setattr(settings, "rate_limit_assistant_per_day", 3)

        for _ in range(3):
            assert request(actor(), cached=False) is not None
        with pytest.raises(HTTPException) as exc:
            request(actor(), cached=False)
        assert exc.value.status_code == 429

    def test_적중이_섞여도_미적중만_센다(self, fake_redis, monkeypatch):
        settings = get_settings()
        monkeypatch.setattr(settings, "rate_limit_assistant_per_min", 100_000)
        monkeypatch.setattr(settings, "rate_limit_assistant_per_day", 3)

        for cached in (True, False, True, True, False, True):
            request(actor(), cached=cached)

        daily_key = next(k for k in fake_redis.counts if ":assistant_daily:" in k)
        assert fake_redis.counts[daily_key] == 2

    def test_적중이어도_분당_한도는_센다(self, fake_redis, monkeypatch):
        """분당은 **부하** 한도라 적중도 세야 한다.

        안 세면 캐시되는 질의 하나로 서버를 무한정 두드릴 수 있다. 두 한도가
        **서로 다른 것을 막는다**는 게 이 라운드의 요점이므로, 한쪽만 바꾸고
        다른 쪽이 따라 바뀌지 않았는지 확인한다.
        """
        settings = get_settings()
        monkeypatch.setattr(settings, "rate_limit_assistant_per_min", 2)

        for _ in range(2):
            request(actor(), cached=True)
        with pytest.raises(HTTPException) as exc:
            request(actor(), cached=True)
        assert exc.value.status_code == 429


class TestCheckAndConsumeAgreeOnTheKey:
    """**검사와 증가가 다른 키를 쓰면 한도가 조용히 사라진다.**

    검사는 영원히 0을 읽고 증가는 아무도 안 보는 카운터를 올린다 — 정상 응답만
    나오므로 아무도 눈치채지 못한다. 이 파일이 `EXPIRE` 규칙을 회귀로 박아둔
    것과 같은 종류의 침묵이라, 키 일치도 같이 박는다.
    """

    def test_같은_키를_본다(self, fake_redis, monkeypatch):
        settings = get_settings()
        monkeypatch.setattr(settings, "rate_limit_assistant_per_min", 100_000)
        monkeypatch.setattr(settings, "rate_limit_assistant_per_day", 1)

        # 1회 소비하면 다음 요청은 막혀야 한다. 키가 어긋나면 안 막힌다.
        request(actor(), cached=False)
        with pytest.raises(HTTPException):
            request(actor(), cached=False)

    def test_다른_사용자는_안_막힌다(self, fake_redis, monkeypatch):
        # 위 검사가 "전부 막는다"로 통과하는 걸 막는 대조군이다.
        settings = get_settings()
        monkeypatch.setattr(settings, "rate_limit_assistant_per_min", 100_000)
        monkeypatch.setattr(settings, "rate_limit_assistant_per_day", 1)

        request(actor(user_id=3), cached=False)
        assert request(actor(user_id=4), cached=False) is not None


class TestPeekBoundary:
    """`_peek` 는 올리기 전에 보므로 `_hit` 과 부등호가 하나 다르다.

    `<=` 로 옮겨 적으면 한도가 **1 늘어난다.** 조용히 틀리는 자리라 박아둔다.
    """

    def test_한도_직전까지_통과한다(self, fake_redis):
        fake_redis.counts["k"] = 49
        allowed, _ = asyncio.run(rate_limit._peek("k", 50, 100))
        assert allowed

    def test_한도에_도달하면_막는다(self, fake_redis):
        fake_redis.counts["k"] = 50
        allowed, _ = asyncio.run(rate_limit._peek("k", 50, 100))
        assert not allowed

    def test_키가_없으면_창_길이를_돌려준다(self, fake_redis):
        # TTL 이 음수(키 없음)일 때 그대로 내보내면 `Retry-After: -1` 이 나간다.
        allowed, retry_after = asyncio.run(rate_limit._peek("없는키", 50, 4321))
        assert allowed and retry_after == 4321
