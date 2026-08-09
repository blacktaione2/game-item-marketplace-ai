"""요청 한도 (ADR-0024).

## 무엇을 막는가

**LLM을 부르는 경로 전부**다. 지금은 `/api/assistant`, `/api/assistant/stream`,
`/api/search` 셋이고, 셋 다 `limit_assistant` + `consume_daily` 한 쌍을 쓴다.
검색 분기는 LLM을 2회 부르고, 그게 실측 지연의 97%다(ADR-0020).

> **이 문단은 "`/api/assistant` 하나다"라고 적혀 있었고, 그게 틀렸다.**
> 근거로 "조회 계열은 싸다"를 들었는데 — `/api/forecast` 와
> `/api/anomaly/detect` 는 실제로 싸다(둘 다 `llm_client` 를 주입받지도
> 않는다). 하지만 **`/api/search` 는 조회 계열이 아니다.** `run_search` 가
> `understand_query` 와 `judge_in_domain` 을 부르므로 호출당 LLM 2회다.
> 그래서 토큰만 있으면 3계층을 전부 우회할 수 있었다.
>
> **한 문장이 세 경로를 뭉뚱그리면, 예외는 그 일반화 안에 숨는다.** 이 저장소가
> "한쪽만 선언된 설정은 결정이 아니라 누락이다"라고 적어둔 것의 사촌이다 —
> 이번엔 선언이 아니라 *근거*가 이웃까지 덮었다.
>
> 재발 방지는 열거가 아니라 **유도**다. `tests/test_llm_route_metering.py` 는
> "LLM 클라이언트를 주입받는 라우트는 한도 의존성도 가져야 한다"를 라우트
> 시그니처에서 읽어낸다 — 새 LLM 경로가 생기면 목록을 고치지 않아도 걸린다.

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


def seconds_until_kst_midnight(kst_now: datetime) -> int:
    """KST 자정까지 남은 초.

    일일 키의 만료를 **86,400초가 아니라 이 값으로** 건다. 둘 다 "하루"지만
    기준이 다르다 — 86,400은 *첫 호출 시점*부터 24시간이고, 이건 *자정*까지다.
    키 이름에 날짜가 박혀 있어 리셋 시각은 어차피 자정이므로, 86,400은 리셋과
    무관한 청소용 값이었다.

    그런데 그 값이 **`Retry-After` 로 새어나가고 있었다.** 01:00에 첫 호출한
    사용자가 23:00에 한도를 넘기면 TTL이 79,200이라 "22시간 뒤에 오라"고 답한다.
    실제로는 **1시간 뒤** 자정에 풀린다 — 최대 하루 가까이 과장된다.

    `cache/policy.py::_until_midnight` 과 같은 계산이다. 그쪽은 시세 예측이
    일별 시리즈라 날짜가 바뀌면 근거가 바뀌기 때문이고, 여기는 키가 날짜로
    갈리기 때문이다 — 이유는 다르지만 "자정에 끝난다"는 성질이 같다.

    > **이 문장은 한동안 거짓이었다.** 그쪽은 `datetime.now()`(프로세스 로컬)를
    > 쓰고 여기는 명시적 KST 라, 배포 컨테이너(UTC)에서 두 자정이 **9시간
    > 어긋나** 있었다. 지금은 양쪽 다 KST 다. 같은 계산이라고 **적어두는 것**과
    > 같은 계산인 것은 별개이고, 시간대는 한쪽만 정해두면 나머지도 그렇겠거니
    > 읽힌다.
    """
    tomorrow = (kst_now + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return max(int((tomorrow - kst_now).total_seconds()), 1)


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


async def _peek(key: str, limit: int, window_seconds: int) -> tuple[bool, int]:
    """올리지 **않고** 확인만 한다. (통과 여부, 남은 초).

    `_hit` 과 경계가 하나 다르다. `_hit` 은 올린 뒤라 N번째 요청의 `count` 가 N
    이므로 `count <= limit`, 이쪽은 올리기 전이라 `count < limit` 이다. 둘 다
    한도만큼 통과시킨다 — 부호를 맞춰 옮기다 하나씩 어긋나기 쉬운 자리다.
    """
    redis = get_redis_client()
    raw = await redis.get(key)
    count = int(raw) if raw else 0
    ttl = await redis.ttl(key)
    # 키가 아직 없으면 TTL 이 음수다. 그때 `Retry-After` 는 창 전체 길이가 맞다.
    return count < limit, max(ttl if ttl > 0 else window_seconds, 1)


def _daily_key(actor: Actor, kst_now: datetime, daily: int) -> str:
    """일일 카운터 키.

    **검사(`limit_assistant`)와 증가(`consume_daily`)가 이 함수를 함께 써야
    한다.** 둘이 다른 키를 만들면 검사는 영원히 0을 읽고 증가는 아무도 안 보는
    카운터를 올린다 — **한도가 조용히 사라지고 정상 응답만 나온다.** 이 파일이
    `EXPIRE` 를 창의 첫 요청에만 거는 이유와 같은 종류의 침묵이다.

    **날짜를 키에 넣어 한국시간 자정에 리셋한다** (ADR-0031). Redis 만료에
    맡기면 창이 "그 사용자의 첫 호출" 기준이라 사람마다 리셋 시각이 다르고
    예측이 안 된다. 날짜가 키에 있으면 "한국시간 자정"이라고 설명할 수 있다.
    """
    return (
        f"ratelimit:assistant_daily:{daily}/1d:{kst_now:%Y-%m-%d}:"
        f"{actor.tenant_code}:{actor.user_id}"
    )


def _kst_now() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=9)


async def consume_daily(actor: Actor) -> None:
    """일일 예산을 1 소비한다.

    **일일 한도만 요청 뒤로 옮겼다** (ADR-0044). 분당 한도는 그대로 요청 앞에서
    올린다 — 목적이 다르기 때문이다.

    | | 목적 | 캐시 적중 |
    |---|---|---|
    | 20회/분 | 부하·남용 차단 | **센다.** 적중도 서버 일이고, 안 세면 캐시되는 질의로 무한정 두드릴 수 있다 |
    | 50회/일 | **LLM 비용** | **안 센다.** 비용 0인 요청이 비용 한도를 깎는 건 앞뒤가 안 맞는다 |

    이론이 아니라 실제로 닿던 경로다. 프론트가 배지를 정직하게 유지하려고
    `staleTime` 을 안 걸어서(ADR-0037), **아이템 상세에 들어갔다 돌아올 때마다
    재요청**이 나간다. 서버는 캐시 적중으로 25.9ms 에 답하는데 예산은 깎였다.

    **대가**: 검사와 증가 사이에 동시 요청이 끼면 하루 경계에서 몇 건 샌다.
    이 파일이 이미 고정 윈도우의 2배 누수를 하루 단위에서 수용하고 있으므로
    (위 `limit_assistant` 주석) 같은 크기의 대가다.

    실패하면 **통과시킨다** — 리미터는 비용 방어이지 정합성 장치가 아니다.
    다만 조용히 넘기지 않는다.
    """
    settings = get_settings()
    if not settings.rate_limit_enabled:
        return
    kst_now = _kst_now()
    key = _daily_key(actor, kst_now, settings.rate_limit_assistant_per_day)
    try:
        redis = get_redis_client()
        if await redis.incr(key) == 1:
            await redis.expire(key, seconds_until_kst_midnight(kst_now))
    except Exception:
        logger.warning("일일 한도 증가 실패 — 통과시킨다", exc_info=True)


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
    #
    # 고정 윈도우의 2배 한계를 하루 단위에서도 그대로 받는다 — 자정 전후로 몰리면
    # 짧은 시간에 100회가 가능하다(ADR-0024가 분 단위에서 기록한 그 현상). 수용하는
    # 이유는 **실제 비용 상한이 OpenAI 월 한도**이고 이 계층은 정밀한 적대적 방어가
    # 아니라 평범한 남용을 막는 것이기 때문이다. 정확히 막으려면 슬라이딩 로그가
    # 필요한데 100 vs 50 차이에 새 구조를 들일 값어치가 없다.
    kst_now = _kst_now()
    daily_key = _daily_key(actor, kst_now, daily)

    try:
        allowed, retry_after = await _hit(key, limit, 60)
        if allowed:
            # 분당 한도를 통과한 요청만 일일 예산을 본다. 먼저 보면 분당에 막힌
            # 요청까지 일일 한도를 갉아먹는다.
            #
            # **여기서는 확인만 하고 올리지 않는다** (ADR-0044). 올리는 것은
            # 응답을 본 뒤 `consume_daily()` 가 하고, **캐시 적중이면 아예
            # 올리지 않는다** — 일일 한도는 LLM 비용을 막는 계층인데 비용 0인
            # 요청이 예산을 깎고 있었다. 자세한 근거는 `consume_daily` 에.
            #
            # **86,400 이 아니라 자정까지 남은 초.** 리셋은 어차피 자정인데
            # TTL 이 `Retry-After` 로 나가므로, 24시간을 걸면 최대 하루 가까이
            # 과장된 대기 시간을 알려주게 된다.
            allowed, retry_after = await _peek(
                daily_key, daily, seconds_until_kst_midnight(kst_now)
            )
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
