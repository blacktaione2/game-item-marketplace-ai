"""요청 한도 (ADR-0024).

## 무엇을 막는가

`/api/assistant` 하나다. **실제로 돈이 나가는 유일한 경로**이기 때문이다 —
검색 분기는 LLM을 2회 부르고, 그게 실측 지연의 97%다(ADR-0020). 조회 계열은
싸고, 지금 한도를 걸 근거가 없다.

## 고정 윈도우를 쓴다 — 한계를 알고 쓴다

`INCR` + `EXPIRE` 두 번이 전부다. 시맨틱 캐시가 쓰는 `redis.asyncio`를 그대로
재사용하므로 새 의존성이 없다.

**경계에서 최대 2배까지 통과한다.** 창이 끝나기 직전에 한도만큼, 다음 창이
시작하자마자 또 한도만큼 들어오면 짧은 구간에 2배가 나간다. 슬라이딩 윈도우로
막으려면 Lua가 필요한데, 목적이 **비용 폭주 차단**이지 "정확한 분당 N"이
아니라서 그 정밀도를 사지 않았다. 백엔드가 토큰 버킷(Redisson)인 것과 다른
이유는 단순히 각자 **이미 가진 것**을 쓰기 때문이다.

## Redis가 죽으면

**통과시킨다.** 리미터는 비용 방어이지 정합성 장치가 아니다 — Redis 장애로
서비스 전체가 멈추는 쪽이 더 나쁘다. 대신 그 사실이 조용하지 않도록 경고를
남긴다. (구매 경로의 분산 락은 반대다. 거긴 정합성이라 실패하면 거절한다.)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException

from app.core.auth import Actor, require_actor
from app.core.config import get_settings
from app.core.metrics import record_rate_limited
from app.services.cache.dependencies import get_redis_client

logger = logging.getLogger(__name__)


async def _hit(key: str, limit: int, window_seconds: int) -> tuple[bool, int]:
    """고정 윈도우 카운터를 1 올리고 (통과 여부, 남은 초)를 준다."""
    redis = get_redis_client()
    count = await redis.incr(key)
    if count == 1:
        # 이 창의 첫 요청일 때만 만료를 건다. 매번 걸면 창이 계속 밀려서
        # 한도가 사실상 사라진다 — 고정 윈도우 구현에서 흔한 실수다.
        await redis.expire(key, window_seconds)
    ttl = await redis.ttl(key)
    return count <= limit, max(ttl, 1)


async def limit_assistant(actor: Actor = Depends(require_actor)) -> Actor:
    """`/api/assistant` 한도. 키는 **테넌트 + 사용자**다.

    테넌트를 키에 넣는 이유는 한 테넌트의 사용자가 다른 테넌트 사용자의 한도를
    잡아먹지 않게 하기 위해서다. id 공간이 테넌트별로 갈리므로 사용자 id만으로는
    충돌한다.
    """
    settings = get_settings()
    if not settings.rate_limit_enabled:
        return actor

    limit = settings.rate_limit_assistant_per_min
    key = f"ratelimit:assistant:{limit}/60s:{actor.tenant_code}:{actor.user_id}"

    daily = settings.rate_limit_assistant_per_day
    # **날짜를 키에 넣어 한국시간 자정에 리셋한다** (ADR-0031).
    #
    # Redis 만료에 맡기면 창이 "그 사용자의 첫 호출" 기준이라 사람마다 리셋 시각이
    # 다르고 예측이 안 된다. 날짜가 키에 있으면 "한국시간 자정"이라고 설명할 수 있다.
    #
    # 고정 윈도우의 2배 한계를 하루 단위에서도 그대로 받는다 — 자정 전후로 몰리면
    # 짧은 시간에 100회가 가능하다(ADR-0024가 분 단위에서 기록한 그 현상). 수용하는
    # 이유는 **실제 비용 상한이 OpenAI 월 한도**이고 이 계층은 정밀한 적대적 방어가
    # 아니라 평범한 남용을 막는 것이기 때문이다. 정확히 막으려면 슬라이딩 로그가
    # 필요한데 100 vs 50 차이에 새 구조를 들일 값어치가 없다.
    kst_date = (datetime.now(timezone.utc) + timedelta(hours=9)).strftime("%Y-%m-%d")
    daily_key = (
        f"ratelimit:assistant_daily:{daily}/1d:{kst_date}:"
        f"{actor.tenant_code}:{actor.user_id}"
    )

    try:
        allowed, retry_after = await _hit(key, limit, 60)
        if allowed:
            # 분당 한도를 통과한 요청만 일일 카운터를 올린다. 먼저 올리면 분당에
            # 막힌 요청까지 일일 한도를 갉아먹는다.
            allowed, retry_after = await _hit(daily_key, daily, 86_400)
    except Exception:
        # 조용히 열지 않는다. 열되 남긴다.
        logger.warning("rate limit 검사 실패 — 통과시킨다", exc_info=True)
        return actor

    if not allowed:
        record_rate_limited(actor.tenant_code, "assistant")
        raise HTTPException(
            status_code=429,
            detail="요청이 너무 잦습니다. 잠시 후 다시 시도해주세요.",
            headers={"Retry-After": str(retry_after)},
        )
    return actor
