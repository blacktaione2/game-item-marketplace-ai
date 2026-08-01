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
from collections.abc import Callable
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
        digest = hashlib.sha1(query.strip().encode("utf-8")).hexdigest()[:16]
        return f"{self._namespace(tenant_code)}:e:{digest}"

    # --- 조회 ------------------------------------------------------------
    async def lookup(
        self, tenant_code: str, query: str, embed: Callable[[], list[float]]
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
        """
        entries, keys = await self._load_entries(tenant_code)
        if not entries:
            return None

        exact_key = self.entry_key(tenant_code, query)
        for entry, key in zip(entries, keys):
            if key == exact_key:
                # **여기서 반환하면 embed()는 불리지 않는다.** 그게 이 설계의 요점이고
                # tests/test_cache.py 가 "부르면 터지는" 콜러블로 고정한다.
                return _hit(entry, key, 1.0, "exact")

        vectors = np.stack([entry["_vector"] for entry in entries])
        query_vector = _normalize(np.asarray(embed(), dtype=np.float32))
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


def _normalize(vector: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vector))
    return vector if norm == 0 else (vector / norm).astype(np.float32)


def _encode(vector: np.ndarray) -> str:
    return base64.b64encode(vector.astype(np.float32).tobytes()).decode("ascii")


def _decode(encoded: str) -> np.ndarray:
    return np.frombuffer(base64.b64decode(encoded), dtype=np.float32)
