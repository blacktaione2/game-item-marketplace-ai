"""동기 CPU 호출을 이벤트 루프 밖으로 내보내는 유일한 이음매.

이 서버의 무거운 계산은 전부 동기 라이브러리다 — sentence-transformers(torch),
ONNX Runtime, KoELECTRA. `async def` 핸들러 안에서 그냥 부르면 계산이 끝날
때까지 **루프 전체가 멈춘다.** 그동안 다른 요청의 콜백은 완료된 I/O를 들고도
실행되지 못한다(ADR-0026에서 이 현상을 처음 실측했다).

## `asyncio.to_thread`를 쓰지 않는 이유

`to_thread`는 기본 실행기를 쓰고 그 크기는 `min(32, cpu_count + 4)`다 —
개발기 16, 배포 대상(4 OCPU) 8. **측정상 둘 다 손해 구간이다.** 같은 작업
8건을 워커 수만 바꿔 돌린 결과(12코어 개발기):

| 워커 | encode_one (torch) | rerank (ONNX RT) |
|---:|---:|---:|
| 1 | 510 ms | 1,019 ms |
| 2 | 572 ms (+12%) | 823 ms (−19%) |
| 4 | 943 ms (+85%) | 705 ms (−31%) |

torch는 이미 내부적으로 코어 수만큼 intra-op 스레드를 쓰기 때문에 바깥
스레드를 늘리면 초과 구독으로 **느려진다.** 검색 1건은 encode 1회 + rerank
1회이므로 절대시간으로 합산하면 워커 2가 −134ms(이득), 워커 4가 +119ms(손해)다.
그래서 크기를 우리가 정한다.

## 이 풀이 하는 일과 하지 않는 일

**하는 일은 루프 응답성 하나다.** CPU 작업이 빨라지지 않는다 — torch는 오히려
동시 실행에서 느려진다. 처리량이 늘기를 기대하고 크기를 키우면 위 표의 오른쪽
칸으로 간다.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from typing import Any, Callable, TypeVar

from app.core.config import get_settings

T = TypeVar("T")


@lru_cache
def _pool() -> ThreadPoolExecutor:
    return ThreadPoolExecutor(
        max_workers=get_settings().cpu_pool_workers,
        thread_name_prefix="cpu",
    )


async def run_cpu(fn: Callable[..., T], *args: Any) -> T:
    """동기 CPU 함수를 전용 스레드에서 실행하고 결과를 기다린다.

    왕복 비용은 실측 0.211ms(p95 0.35ms)다. 그래서 **격리 median 5ms 미만인
    호출은 감싸지 않는다** — 10ms 티커로는 그만한 블로킹을 정상 지터와 구분할
    수 없어서 고쳤다는 것을 보일 방법이 없고, 순비용만 남는다. 이 기준으로
    오토인코더 점수(0.31ms)와 LSTM 추론(0.45ms)은 제외했다.
    """
    return await asyncio.get_running_loop().run_in_executor(_pool(), fn, *args)
