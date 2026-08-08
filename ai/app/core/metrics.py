"""Prometheus 메트릭 정의와 `timings` → 히스토그램 변환.

## 왜 한 곳에서 변환하는가

파이프라인이 이미 단계별 소요 시간을 `timings` dict로 재고 있다(응답 본문에도
그대로 나간다). 단계마다 계측 코드를 흩뿌리는 대신 **응답을 만들 때 한 번**
dict를 히스토그램으로 옮긴다. 계측 지점이 하나뿐이라 새 단계가 생겨도
`_STAGE_BY_KEY`에 한 줄만 추가하면 된다.

## 라벨 규칙 — 카디널리티 상한

`tenant` × `intent`(6) × `stage`(16). 테넌트가 O(10)까지는 안전하다.

> 이 숫자는 `_STAGE_BY_KEY` 를 세면 나온다. 11 로 적혀 있었는데 그 뒤
> `cache_encode`·`cache_lookup`(ADR-0025)·`domain_gate`(ADR-0039) 등이 늘었다 —
> **상한을 논하는 주석은 늘어나는 쪽을 세고 있으므로 같이 늘어나야 한다.**

**절대 라벨로 쓰지 않는다**: `item_id` · `trade_id` · `user_id` · 질의 문자열.
전부 무한히 늘어나는 값이고, 하나라도 라벨에 들어가면 시계열이 폭발한다.
"어떤 아이템이 느렸나"는 메트릭이 아니라 로그로 답할 문제다.
"""

from __future__ import annotations

from typing import Any

from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest

# 기본 레지스트리를 쓰지 않는다 — 테스트가 서로 오염되지 않게 격리한다.
REGISTRY = CollectorRegistry()

# LLM 호출은 초 단위, ES·리랭커는 밀리초 단위라 버킷을 넓게 잡는다.
_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0)

stage_duration = Histogram(
    "ai_stage_duration_seconds",
    "파이프라인 단계별 소요 시간",
    labelnames=("stage", "tenant"),
    buckets=_BUCKETS,
    registry=REGISTRY,
)

requests_total = Counter(
    "ai_requests_total",
    "어시스턴트 요청 수",
    labelnames=("tenant", "intent", "outcome"),
    registry=REGISTRY,
)

llm_calls_total = Counter(
    "ai_llm_calls_total",
    "요청당 발생한 LLM 호출 수의 누적",
    labelnames=("tenant", "intent"),
    registry=REGISTRY,
)

cache_lookups_total = Counter(
    "ai_cache_lookups_total",
    "시맨틱 캐시 조회 결과",
    labelnames=("tenant", "result"),
    registry=REGISTRY,
)

# 한도 초과 (ADR-0024). **이건 record_response()를 지나지 않는다** — 거절이
# 파이프라인 진입 전에 끝나므로 응답 자체가 만들어지지 않는다. 그래서 계측 지점이
# 하나라는 원칙의 예외이고, 예외인 이유를 여기 적어둔다.
#
# `path`는 경로 패턴이지 실제 URL이 아니다. 라벨은 값의 종류가 유한해야 한다.
rate_limited_total = Counter(
    "ai_rate_limited_total",
    "한도 초과로 거절된 요청 수",
    labelnames=("tenant", "path"),
    registry=REGISTRY,
)

# 프로바이더별 실제 호출 수 (ADR-0042). **계측 지점 원칙의 두 번째 예외이고,
# 여기도 예외인 이유를 적어둔다.**
#
# ## 왜 record_response() 로는 안 되나
#
# 이 사실이 **응답 모양이 아니다.** 에이전트 한 요청이 `chat()` 을 다섯 번 부르고
# 그중 둘만 폴백일 수 있다 — 응답 하나에 프로바이더가 섞인다. `record_response()`
# 는 요청당 한 번 도는 자리라 이걸 표현할 방법이 없다.
#
# ## 왜 폴백 래퍼가 아니라 각 클라이언트에서 세나
#
# 키가 없으면 래퍼를 아예 만들지 않는다(ADR-0042). 래퍼에서 세면 **폴백이 없는
# 구성에서는 아무것도 안 잡힌다** — 정작 프로바이더가 하나뿐이라 더 취약한 쪽이다.
#
# ## 무엇을 고치는가 — 새 기능이 아니라 내가 만든 드리프트다
#
# `ai_llm_calls_total` 은 *"이 요청이 LLM 을 몇 번 썼나"* 를 센다. 프로바이더가
# 하나일 땐 그게 곧 OpenAI 사용량이었는데, **폴백이 붙는 순간 그 해석이 깨졌다.**
# 어디에 청구됐는지 말해주지 않는다.
#
# 부수 효과가 하나 더 있다. `ai_llm_calls_total` 은 응답의 `llm_calls` **상수**에서
# 오고 이건 **실제 호출**을 센다 — **둘이 어긋나면 그 상수가 틀린 것**이다.
# ADR-0039·0041 에서 그 값을 세 번 손으로 고쳤으니 교차 검증이 생기는 셈이다.
llm_provider_calls_total = Counter(
    "ai_llm_provider_calls_total",
    "프로바이더별 실제 LLM 호출 수",
    # 라벨 카디널리티 2 x 2 = 4. 테넌트를 넣지 않는다 — 프로바이더 장애는
    # 테넌트와 무관하고, 넣으면 시계열만 늘린다.
    labelnames=("provider", "outcome"),
    registry=REGISTRY,
)


def record_llm_call(provider: str, ok: bool) -> None:
    """LLM 호출 1건을 센다. 클라이언트 구현이 직접 부른다."""
    llm_provider_calls_total.labels(
        provider=provider, outcome="ok" if ok else "failed"
    ).inc()

# `timings` 키 → 메트릭의 stage 라벨. 키에서 `_ms`를 떼는 규칙이 아니라
# 명시적 표를 쓴다 — 새 키가 생겼을 때 조용히 통과하지 않고 눈에 띄게 하려고.
_STAGE_BY_KEY: dict[str, str] = {
    "cache_ms": "cache",
    # 캐시 단계의 분해(ADR-0025). 적중 경로에서 임베딩이 지배적인지 판정하려고
    # 갈랐고, 지연화 이후에는 **적중 시 cache_encode가 0**이어야 한다 — 회귀 신호다.
    "cache_encode_ms": "cache_encode",
    "cache_lookup_ms": "cache_lookup",
    "routing_ms": "routing",
    "execution_ms": "execution",
    "query_understanding_ms": "query_understanding",
    # 도메인 판정 (ADR-0039). 질의이해와 **병렬**로 나가므로 둘을 더하면 실제
    # 지연보다 크게 나온다 — 검색 전체 시간은 `execution_ms` 로 봐야 한다.
    "domain_gate_ms": "domain_gate",
    "embedding_ms": "embedding",
    "retrieval_ms": "retrieval",
    "rerank_ms": "rerank",
    "explain_ms": "explain",
    "window_ms": "forecast_window",
    "inference_ms": "forecast_inference",
    "scoring_ms": "anomaly_scoring",
    "agent_llm_ms": "agent_llm",
    "agent_tool_ms": "agent_tool",
}


def stage_for(key: str) -> str | None:
    return _STAGE_BY_KEY.get(key)


def record_timings(tenant: str, timings: dict[str, float]) -> None:
    """`timings` dict를 히스토그램으로 옮긴다. 모르는 키는 조용히 버린다."""
    for key, value in timings.items():
        stage = _STAGE_BY_KEY.get(key)
        if stage is None:
            continue
        stage_duration.labels(stage=stage, tenant=tenant).observe(value / 1000.0)


def cache_result(cache: dict[str, Any]) -> str:
    """캐시 조회 결과를 라벨 값으로. 적중 종류를 구분해야 의미가 있다."""
    if not cache.get("hit"):
        return "miss"
    return f"hit_{cache.get('match_type', 'unknown')}"


def record_response(tenant: str, response: dict[str, Any]) -> None:
    """응답 하나가 만들어질 때마다 호출. 계측 지점은 여기 하나뿐이다."""
    intent = str(response.get("intent", "unknown"))

    record_timings(tenant, response.get("timings", {}))

    requests_total.labels(
        tenant=tenant, intent=intent, outcome=_outcome(response)
    ).inc()
    llm_calls_total.labels(tenant=tenant, intent=intent).inc(
        response.get("llm_calls", 0)
    )
    cache_lookups_total.labels(
        tenant=tenant, result=cache_result(response.get("cache", {}))
    ).inc()


def _outcome(response: dict[str, Any]) -> str:
    """성공/실패가 아니라 **무엇이 일어났는지**를 센다.

    0건과 도구 실패는 에러가 아니지만 부하테스트에서 구분해서 봐야 한다 —
    0건이 늘면 캐시 미저장 경로가 늘고, 도구 실패가 늘면 에이전트가 재시도한다.
    """
    # **도메인 밖 거절은 0건보다 먼저 본다** (ADR-0039). 두 플래그가 같이 설 일은
    # 없지만, 순서를 정해두지 않으면 나중에 하나가 다른 하나를 가린다.
    #
    # 이 열이 있어야 게이트를 **운영에서** 볼 수 있다. 오거부율은 배포 전에 한 번
    # 쟀을 뿐이고, 실제 사용자 질의 분포는 평가셋과 다르다 — 이 값이 갑자기 늘면
    # 게이트가 멀쩡한 질의를 막고 있다는 뜻이다.
    if response.get("out_of_domain"):
        return "out_of_domain"
    if response.get("no_results"):
        return "no_results"
    if response.get("tool_failures"):
        return "tool_failure"
    # **설명 LLM 이 죽어 결정적 문장으로 내려앉았다** (ADR-0041). 사용자는 답을
    # 받았으므로 실패가 아니지만 `ok` 도 아니다 — 이 값이 늘면 LLM 프로바이더가
    # 흔들리고 있다는 뜻이고, 그건 500 이 안 나기 때문에 **다른 데서는 안 보인다.**
    #
    # 앞의 셋과 같이 설 일은 없다(각각 다른 분기에서 나온다). 그래도 순서를
    # 정해두는 이유는 위 주석과 같다 — 나중에 하나가 다른 하나를 가리지 않게.
    if response.get("degraded"):
        return "degraded"
    return "ok"


def record_rate_limited(tenant: str, path: str) -> None:
    """한도 초과 거절. `record_response()`와 별개인 이유는 위 카운터 정의 참고."""
    rate_limited_total.labels(tenant=tenant, path=path).inc()


def render() -> bytes:
    return generate_latest(REGISTRY)
