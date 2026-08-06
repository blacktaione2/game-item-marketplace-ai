"""배포 대상(ARM)에서 동기 CPU 단계를 재측정한다.

실행: docker exec gimp-ai python -m scripts.benchmark_cpu_stages

## 왜 다시 재는가

ADR-0028 이 `cpu_pool_workers=2` 를 고른 근거 표는 **12코어 개발기에서 나왔다.**
배포 대상은 4 OCPU ARM 이고, 그 표의 논리(“torch 는 이미 내부적으로 코어 수만큼
intra-op 스레드를 쓰므로 바깥 스레드를 늘리면 초과 구독”)는 **코어 수에 직접
의존한다.** 12코어에서 4워커가 손해였다고 4코어에서도 그렇다는 보장이 없다.

ADR-0032 도 마찬가지다. 리랭커의 int8 ONNX 가 aarch64 에서 **로드되고 추론된다**
까지만 확인했고 **얼마나 걸리는지는 재지 않았다.** 지연 예산을 이유로 양자화까지
한 컴포넌트인데 배포 대상에서의 수치가 없다.

## LLM 을 부르지 않는다

임베딩·리랭커는 전부 로컬 모델이다. 이 스크립트의 비용은 0원이고 OpenAI 한도와
무관하다. 그래서 몇 번을 돌려도 된다 — 그게 아래 방법론의 전제다.

## 방법론

1. **워밍업 먼저.** 첫 호출은 지연 로딩을 포함한다(실측 42.9초 요청의 82%가
   모델 로딩이었다, ADR-0019). 워밍업 없는 첫 표본은 모델 로딩 시간이다.
2. **잡음 바닥을 먼저 잰다.** 부하 0인 상태의 티커 지연을 재고 그 위에서
   판단한다. ADR-0028 은 이 순서를 어겨서 판정선(20ms)이 잡음 바닥(16.34ms)
   안에 놓였다.
3. **median 과 p99 를 본다. max 로 판단하지 않는다.** max 는 표본 하나가
   정한다 — 같은 코드가 32.75ms 와 17.89ms 를 냈던 이유가 그거다.
4. **각 설정을 2회 돌린다.** 한 번만 돌린 수치로는 설정 간 차이와 실행 간
   변동을 구분할 수 없다.
5. **처리량이 아니라 응답성을 본다.** 스레드풀은 CPU 작업을 빠르게 하지
   않는다. 늘어나야 하는 건 루프 응답성이고, 총 소요시간은 **오히려 나빠질 수
   있다**(초과 구독). 두 값을 같이 낸다.
"""

from __future__ import annotations

import asyncio
import platform
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor

# **표준출력을 UTF-8 로 고정한다.** Windows 콘솔 기본 코덱(cp949)에서는 한글이
# 깨지는 데 그치지 않고 em dash 같은 문자에서 **UnicodeEncodeError 로 죽는다** —
# 이 스크립트를 로컬에서 검증하다 실제로 겪었다. 배포 대상(리눅스)에서는 필요
# 없지만, 한쪽에서만 돌아가는 측정 도구는 비교를 못 하게 만든다.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.corpus import ALL_ITEMS
from app.services.search.embedding import get_embedding_service
from app.services.search.reranker import get_reranker

# ADR-0028 이 쓴 것과 같은 작업 수. 표를 나란히 읽으려면 같아야 한다.
JOBS = 8
# 설정당 반복 횟수.
#
# **처음엔 2였는데 부족했다.** ARM 첫 실행에서 rerank 2워커가 1420ms 와 1009ms
# 를 냈다 — 411ms 차이면 설정 간 차이(1워커 897ms vs 4워커 999ms)보다 크다.
# 즉 2회로는 **설정을 비교할 수 없고**, 그 상태에서 나온 순위는 실행 순서를
# 읽은 것이다. 스윕 전체가 10초 남짓이라 반복을 늘리는 비용이 사실상 없다.
RUNS = 7
# 격리 지연 표본 수. 워밍업 이후.
SAMPLES = 20
TICK_INTERVAL = 0.01
QUERY = "5만원 이하 강화된 검 찾아줘"


def pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(int(len(ordered) * p / 100), len(ordered) - 1)
    return ordered[index]


def describe(name: str, values: list[float], unit: str = "ms") -> str:
    return (
        f"  {name:<22} median {statistics.median(values):7.2f}{unit}"
        f"  p99 {pct(values, 99):7.2f}{unit}"
        f"  max {max(values):7.2f}{unit}  (n={len(values)})"
    )


# --- 대상 작업 -------------------------------------------------------------
#
# 실제 검색 1건이 하는 것과 같은 모양으로 만든다 — 임베딩 1회 + 상위 20건 재순위.
#
# **`name` 과 `description` 을 둘 다 넘긴다.** 리랭커의 `_document_text` 가 그 둘을
# 붙여 쓰기 때문이다. 첫 판본은 `name` 만 넘겼는데, 그러면 토크나이저가 보는 길이가
# 실제의 절반쯤이라 **54.71ms 가 나왔다 — ADR-0028 의 103~189ms 와 비교할 수 없는
# 숫자다.** 주석에는 "합성 문자열을 쓰면 실제와 다른 길이를 본다"고 적어놓고 정작
# 필드를 절반만 넘긴 셈이었다.
_DOCS = [
    {"name": item["name"], "description": item["description"]} for item in ALL_ITEMS[:20]
]


def do_encode() -> None:
    get_embedding_service().encode_one(QUERY)


def do_rerank() -> None:
    # rerank 가 리스트를 제자리에서 정렬하므로 매번 사본을 준다.
    get_reranker().rerank(QUERY, [dict(doc) for doc in _DOCS])


# --- 이벤트 루프 지연 ------------------------------------------------------
async def _ticker(stop: asyncio.Event) -> list[float]:
    """10ms 마다 깨어나 **실제로 얼마나 늦게 깨어났는지**를 기록한다.

    동기 CPU 호출이 루프를 막고 있으면 이 값이 커진다. 스레드로 내보낸 뒤에도
    커지면 내보내기가 안 걸린 것이다.
    """
    lags: list[float] = []
    while not stop.is_set():
        started = time.perf_counter()
        await asyncio.sleep(TICK_INTERVAL)
        lags.append((time.perf_counter() - started - TICK_INTERVAL) * 1000)
    return lags


async def idle_floor(seconds: float = 3.0) -> list[float]:
    """부하 0에서의 티커 지연 = 이 기계의 잡음 바닥."""
    stop = asyncio.Event()
    task = asyncio.create_task(_ticker(stop))
    await asyncio.sleep(seconds)
    stop.set()
    return await task


async def sweep(workers: int, job) -> tuple[float, list[float]]:
    """워커 N개로 같은 작업 JOBS건을 돌리고 (총 소요, 티커 지연)을 낸다."""
    pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="bench")
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    ticker = asyncio.create_task(_ticker(stop))

    started = time.perf_counter()
    await asyncio.gather(*(loop.run_in_executor(pool, job) for _ in range(JOBS)))
    elapsed = (time.perf_counter() - started) * 1000

    stop.set()
    lags = await ticker
    pool.shutdown(wait=True)
    return elapsed, lags


# --- 격리 지연 -------------------------------------------------------------
def isolated(job, samples: int = SAMPLES) -> list[float]:
    timings = []
    for _ in range(samples):
        started = time.perf_counter()
        job()
        timings.append((time.perf_counter() - started) * 1000)
    return timings


async def main() -> None:
    import os

    print("=" * 78)
    print("CPU 단계 측정 — 배포 대상 재검증 (ADR-0028 · ADR-0032)")
    print("=" * 78)
    print(f"  플랫폼   {platform.machine()} / {platform.system()}")
    print(f"  CPU 수   {os.cpu_count()}")
    print()

    print("[워밍업] 모델을 미리 올린다 — 첫 호출은 지연 로딩을 포함한다")
    started = time.perf_counter()
    do_encode()
    encode_load = (time.perf_counter() - started) * 1000
    started = time.perf_counter()
    do_rerank()
    rerank_load = (time.perf_counter() - started) * 1000
    print(f"  임베딩 첫 호출  {encode_load:8.0f}ms")
    print(f"  리랭커 첫 호출  {rerank_load:8.0f}ms")
    print("  (이 둘은 지연 로딩 비용이다. 아래 수치와 섞어 읽지 말 것)")
    print()

    print("[1] 잡음 바닥 — 부하 0에서의 이벤트 루프 지연")
    print("    판정선을 세우기 전에 이걸 먼저 본다. 이 값보다 작은 차이는")
    print("    측정할 수 없다.")
    floor = await idle_floor()
    print(describe("idle ticker", floor))
    print()

    print("[2] 격리 지연 — 한 번에 하나씩, 동시성 없음")
    print("    비교 기준: 개발기 실측 encode_one 15.77ms / rerank 103~189ms")
    print(describe("encode_one", isolated(do_encode)))
    print(describe("rerank (20건)", isolated(do_rerank)))
    print()

    print("[3] 워커 수 스윕 — 같은 작업 8건")
    print("    **총 소요가 줄기를 기대하지 않는다.** 스레드풀은 CPU 작업을")
    print("    빠르게 하지 않는다. 봐야 할 것은 티커 지연이다.")
    for label, job in (("encode_one (torch)", do_encode), ("rerank (ONNX RT)", do_rerank)):
        print(f"\n  {label}")
        print(
            f"    {'워커':<6}{'총 소요 median':>15}{'최소':>9}{'최대':>9}"
            f"{'실행간 폭':>11}{'티커 p99':>11}"
        )
        for workers in (1, 2, 4):
            runs = [await sweep(workers, job) for _ in range(RUNS)]
            elapsed = [e for e, _ in runs]
            lags = [lag for _, lag_list in runs for lag in lag_list]
            spread = max(elapsed) - min(elapsed)
            # **실행간 폭을 같이 낸다.** 이게 설정 간 차이보다 크면 순위를
            # 읽어선 안 된다 — 실제로 첫 ARM 실행이 그 상태였다.
            print(
                f"    {workers:<6}{statistics.median(elapsed):>13.0f}ms"
                f"{min(elapsed):>8.0f}ms{max(elapsed):>8.0f}ms"
                f"{spread:>10.0f}ms{pct(lags, 99):>10.2f}ms"
            )
    print()
    print("=" * 78)
    print("읽는 법")
    print("=" * 78)
    print("  · 총 소요는 워커를 늘려도 줄지 않는 게 정상이다(오히려 늘 수 있다).")
    print("  · 티커 지연이 [1]의 잡음 바닥 근처면 루프가 안 막힌 것이다.")
    print("  · **실행간 폭이 설정 간 차이보다 크면 순위를 읽지 않는다.** 그건")
    print("    설정을 비교한 게 아니라 실행 순서를 읽은 것이다.")
    print("  · 티커 지연이 **음수**로 나오면 그 값은 버린다. Windows 에서 관측된")
    print("    타이머 분해능 artifact 이고, 리눅스에서는 나오지 않아야 한다.")
    print("  · 현재 설정값은 settings.cpu_pool_workers 이며 기본 2다.")


if __name__ == "__main__":
    asyncio.run(main())
