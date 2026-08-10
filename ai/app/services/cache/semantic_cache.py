"""Redis 시맨틱 캐시.

임베딩 유사도로 "같은 질문"을 찾아 LLM 호출을 통째로 건너뛴다. 기획서상
**모든 분기 이전**에 조회한다.

## 왜 Redis에 벡터 인덱스를 안 쓰나

docker-compose의 Redis는 `redis:7-alpine`이라 RediSearch(VECTOR 필드)가 없다.
이미지를 `redis-stack`으로 바꾸면 네이티브 KNN을 쓸 수 있지만 메모리가 늘고,
공유 인프라(4 OCPU/24GB)에 on-demand로 띄우는 운영 방식과 상충한다.

대신 후보를 통째로 가져와 numpy로 코사인을 계산한다. **O(n) 스캔이라는 한계는
명확하다** — 엔트리 수백~수천 건에서는 numpy 연산이 1ms 미만이라 LLM
호출(~1s) 대비 무시할 수 있지만, 규모가 커지면 redis-stack이나 인프로세스
인덱스로 옮겨야 한다. (ADR-0012)

## 테넌트 격리

같은 질의라도 테넌트마다 인덱스와 매물이 달라 답이 다르다. 네임스페이스를
섞으면 **오답을 내보내는 정확성 문제**이지 성능 문제가 아니다. 그래서 키에
tenant_code를 넣고 후보군도 테넌트 안에서만 찾는다.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


class SemanticCache:
    def __init__(
        self,
        redis_client: Any,
        threshold: float,
        max_entries: int,
        version: str,
    ) -> None:
        self._redis = redis_client
        self._threshold = threshold
        self._max_entries = max_entries
        self._version = version

    # --- 키 --------------------------------------------------------------
    def _namespace(self, tenant_code: str) -> str:
        # 버전 세그먼트를 두면 재색인·모델 재학습 때 버전만 올려서 통째로
        # 무효화할 수 있다. 키를 하나씩 지우는 것보다 안전하고 빠르다.
        return f"sc:{tenant_code}:{self._version}"

    def index_key(self, tenant_code: str) -> str:
        return f"{self._namespace(tenant_code)}:ids"

    def entry_key(self, tenant_code: str, query: str) -> str:
        digest = hashlib.sha1(normalize_query(query).encode("utf-8")).hexdigest()[:16]
        return f"{self._namespace(tenant_code)}:e:{digest}"

    # --- 조회 ------------------------------------------------------------
    async def lookup(
        self,
        tenant_code: str,
        query: str,
        embed: Callable[[], Awaitable[list[float]]],
    ) -> dict[str, Any] | None:
        """캐시 조회. 정확 일치 우선, 그 다음 (허용된 의도에 한해) 유사도.

        유사도 매칭을 조건부로 두는 이유는 policy.allows_semantic 주석 참고 —
        요약하면 이 도메인의 질의는 한 글자 차이로 답이 뒤집히는데 문장
        임베딩이 그걸 구분하지 못한다.

        ## 임베딩을 값이 아니라 **콜러블**로 받는다 (ADR-0026)

        정확 일치는 질의 해시 키만 쓰므로 임베딩이 필요 없다. 그런데 값으로
        받으면 호출자가 **미리 계산할 수밖에 없고**, 그 계산은 동기 CPU 호출이라
        `async` 핸들러에서 **이벤트 루프를 통째로 막는다**(실측 15.77ms, 그동안
        10ms 타이머가 평균 17.28ms 밀렸다).

        그 대가는 자기 요청의 15ms에서 끝나지 않는다 — 동시 부하에서는 **다른
        요청의 `await`가 그만큼 밀려** 남의 단계 시간으로 잡힌다. 부하 중
        `cache_lookup`이 48.1ms로 찍혔는데 격리 측정은 1.05ms였다.

        콜러블로 받으면 정확 일치일 때 **호출 자체가 일어나지 않는다.**
        캐시 모듈이 임베딩 서비스를 알게 되는 결합도 생기지 않는다.

        콜러블이 **async**인 이유는 그 다음 라운드다(ADR-0028): 계산이 필요한
        경우에도 이제 전용 스레드로 나가므로 호출자가 `await` 해야 한다.
        미적중 경로의 15ms가 남 대신 자기 스레드에서 소모된다.

        ## 정확 일치는 키 하나만 읽는다

        예전 판본은 **정확 일치도 전 엔트리를 `mget` 으로 끌어와 역직렬화하고
        벡터를 정규화한 뒤에** 해시를 비교했다. 위 문단이 *"정확 일치는 질의 해시
        키만 쓰므로"* 라고 적어둔 것과 실제 동작이 달랐다 — 임베딩 계산만 미루고
        스캔은 그대로였다.

        `entry_key()` 가 (테넌트, 질의)로 결정되므로 **그냥 `GET` 하면 된다.**
        상한이 2,000건이라 지금 규모에서 아끼는 시간은 작지만, 이 경로는 적중
        p95 25.9ms 를 만든 자리이고 **엔트리 수에 비례해 늘어나는 유일한 부분**
        이었다. 만료 엔트리의 지연 정리는 미적중 경로가 계속 맡는다 — 적중만
        계속되는 동안 인덱스가 안 줄지만, 그건 `_trim` 의 상한이 받는다.
        """
        exact_key = self.entry_key(tenant_code, query)
        payload = await self._redis.get(exact_key)
        if payload is not None:
            # **여기서 반환하면 embed()는 불리지 않는다.** 그게 이 설계의 요점이고
            # tests/test_cache.py 가 "부르면 터지는" 콜러블로 고정한다.
            return _hit(json.loads(payload), exact_key, 1.0, "exact")

        entries, keys = await self._load_entries(tenant_code)
        if not entries:
            return None

        vectors = np.stack([entry["_vector"] for entry in entries])
        query_vector = _normalize(np.asarray(await embed(), dtype=np.float32))
        similarities = vectors @ query_vector

        best = int(np.argmax(similarities))
        score = float(similarities[best])
        if score < self._threshold:
            return None

        # 저장된 엔트리의 의도로 게이팅한다. 조회 시점에는 아직 라우팅 전이라
        # 질의의 의도를 모르지만, 후보 쪽 의도는 저장할 때 기록해뒀다.
        from app.services.cache.policy import allows_semantic
        from app.services.router.intents import Intent

        hit = entries[best]
        try:
            if not allows_semantic(Intent(hit["intent"])):
                return None
        except ValueError:
            # 저장될 때는 유효했는데 지금은 없는 의도 이름 — 의도를 개명·삭제하고
            # `cache_version` 을 안 올리면 생긴다. 미적중으로 넘기는 판단은 맞다.
            #
            # **다만 조용히 넘기지 않는다.** 이 저장소는 같은 자리에서 이미
            # 배웠다 — 캐시가 실패했을 때의 증상은 "적중률 0" 하나뿐이고, 그건
            # 캐시가 원래 못 맞히는 것과 **구분되지 않는다**(임계값 0.98).
            # 그래서 fail-open/fail-safe 경로는 기록을 남긴다는 규칙이 있고
            # (`pipeline.py` 의 조회·저장, `core/rate_limit.py`),
            # 이 파일만 로거를 정의해두고 한 번도 쓰지 않았다.
            logger.warning(
                "캐시 항목의 의도 이름을 모른다(%r) — 미적중으로 넘긴다. "
                "의도를 개명했다면 cache_version 을 올리세요.",
                hit.get("intent"),
            )
            return None

        return _hit(hit, keys[best], score, "semantic")

    async def _load_entries(
        self, tenant_code: str
    ) -> tuple[list[dict[str, Any]], list[str]]:
        index_key = self.index_key(tenant_code)
        keys = sorted(
            key.decode() if isinstance(key, bytes) else key
            for key in await self._redis.smembers(index_key)
        )
        if not keys:
            return [], []

        payloads = await self._redis.mget(keys)
        entries: list[dict[str, Any]] = []
        alive: list[str] = []
        expired: list[str] = []

        for key, payload in zip(keys, payloads):
            if payload is None:
                # TTL로 사라진 엔트리 — 인덱스에서 지연 정리한다.
                expired.append(key)
                continue
            entry = json.loads(payload)
            entry["_vector"] = _normalize(_decode(entry["embedding"]))
            entries.append(entry)
            alive.append(key)

        if expired:
            await self._redis.srem(index_key, *expired)

        return entries, alive

    # --- 저장 ------------------------------------------------------------
    async def store(
        self,
        tenant_code: str,
        query: str,
        embedding: list[float],
        response: dict[str, Any],
        intent: str,
        ttl: int,
    ) -> None:
        if ttl <= 0:
            return

        key = self.entry_key(tenant_code, query)
        payload = json.dumps(
            {
                "query": query,
                "embedding": _encode(np.asarray(embedding, dtype=np.float32)),
                "response": response,
                "intent": intent,
            },
            ensure_ascii=False,
        )

        index_key = self.index_key(tenant_code)
        await self._redis.set(key, payload, ex=ttl)
        await self._redis.sadd(index_key, key)
        await self._trim(index_key)

    async def _trim(self, index_key: str) -> None:
        """엔트리 수 상한. O(n) 조회라 무한정 늘리면 조회가 느려진다."""
        size = await self._redis.scard(index_key)
        if size <= self._max_entries:
            return
        # 만료로 이미 사라졌을 키부터 정리되므로, 넘치면 임의로 덜어낸다.
        # LRU가 아니라 근사치다 — 상한을 지키는 것이 목적이다.
        excess = size - self._max_entries
        victims = await self._redis.srandmember(index_key, excess)
        if victims:
            await self._redis.srem(index_key, *victims)
            await self._redis.delete(*victims)

    async def clear(self, tenant_code: str) -> int:
        index_key = self.index_key(tenant_code)
        keys = list(await self._redis.smembers(index_key))
        if keys:
            await self._redis.delete(*keys)
        await self._redis.delete(index_key)
        return len(keys)


def _hit(
    entry: dict[str, Any], key: str, score: float, match_type: str
) -> dict[str, Any]:
    return {
        "response": entry["response"],
        "cached_query": entry["query"],
        "intent": entry["intent"],
        "similarity": round(score, 4),
        "match_type": match_type,
        "key": key,
    }


# 문장 끝 종결 기호와 중복 공백. **의미를 바꾸지 않는 것만** 넣는다.
_TRAILING = "!?.~…。，,、 \t"
_WHITESPACE = re.compile(r"\s+")


def normalize_query(query: str) -> str:
    """캐시 키용 질의 정규화.

    **이건 유사도 매칭이 아니다.** 여전히 정확 일치이고, 다만 "같은 글자"의 범위를
    문장부호와 공백만큼 넓힌다. 유사도를 여는 것과 위험이 전혀 다르다 — ADR-0012 가
    막은 건 `+8`/`+9`, `이상`/`이하` 처럼 **한 글자가 답을 뒤집는** 경우인데, 종결
    기호는 그 축에 없다. `"불꽃의 대검 시세 알려줘!"` 와 `"... 알려줘"` 는 같은 질문이다.

    범위를 좁게 잡은 이유가 여기 있다. 조사·어미·띄어쓰기까지 건드리기 시작하면
    `"100렙 이상"` / `"100렙이상"` 은 되지만 그 다음이 어디서 멈출지 근거가 없어진다.
    지금은 **반례를 만들 수 없는 것만** 넣었다.

    정규화 결과가 비면(질의가 부호뿐이면) 원본을 쓴다. 안 그러면 `"???"` 와 `"!!!"`
    가 같은 키가 된다 — 둘 다 무의미하지만 같은 답을 줄 이유는 없다.
    """
    collapsed = _WHITESPACE.sub(" ", query.strip())
    stripped = collapsed.rstrip(_TRAILING)
    return stripped or collapsed


def _normalize(vector: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vector))
    return vector if norm == 0 else (vector / norm).astype(np.float32)


def _encode(vector: np.ndarray) -> str:
    return base64.b64encode(vector.astype(np.float32).tobytes()).decode("ascii")


def _decode(encoded: str) -> np.ndarray:
    return np.frombuffer(base64.b64decode(encoded), dtype=np.float32)
