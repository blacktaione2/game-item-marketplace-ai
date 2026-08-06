"""통합 진입점 오케스트레이션.

```
질의 → 시맨틱 캐시 조회 (모든 분기 이전, 기획서 97행)
      → Intent Router (룰 → KoELECTRA → 애매하면 에이전트)
      → 의도별 분기 실행
      → 정책에 따라 캐시 저장
```

## 분기가 실행 불가능하면 에이전트로 올린다

시세 예측과 이상거래 탐지는 **id가 있어야 실행된다.** 그런데 `"이거 얼마?"`
처럼 대상을 지칭하지 않는 질의도 룰에서는 시세 문의로 확정된다. 그대로
분기를 태우면 실행이 불가능하다.

그래서 각 분기가 **자기가 실행 가능한지 먼저 확인하고, 안 되면 COMPOUND로
올린다.** 라우터를 완벽하게 만드는 대신 분기가 스스로 물러설 수 있게 하는
쪽이 현실적이다 — 에이전트는 검색부터 시작할 수 있으니 어차피 답을 낸다.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from elasticsearch import AsyncElasticsearch

from app.core.config import get_settings
from app.core.ids import IdSpace
from app.core.metrics import record_response
from app.core.threadpool import run_cpu
from app.services.agent.agent import run_agent
from app.services.anomaly.pipeline import detect_trade
from app.services.cache.dependencies import get_semantic_cache
from app.services.cache.policy import is_cacheable, ttl_seconds
from app.services.forecast.pipeline import forecast_price
from app.services.llm.base import LLMClient
from app.services.router.intents import Intent
from app.services.router.router import route
from app.services.search.embedding import get_embedding_service
from app.services.search.pipeline import search as run_search

logger = logging.getLogger(__name__)

# 지시대명사·평가어만 있고 대상이 없는 질의를 걸러내기 위한 불용어.
# 형태소 분석까지 갈 필요는 없다 — 목적은 "대상을 지칭했는가" 한 가지다.
_STOPWORDS = {
    "이거", "이것", "그거", "그것", "저거", "저것", "이", "그", "저", "얘",
    "뭐", "뭐야", "어때", "어떄", "괜찮", "괜찮나요", "괜찮아", "살만해",
    "적당한거로", "적당", "좋은", "추천", "봐줘", "확인", "판단", "골라줘",
    "나아", "얼마", "가격", "시세", "적정가", "어떻게", "생각해", "문제없지",
    "살까", "말까", "좀", "해줘", "알려줘", "보여줘", "있어", "있나요",
    "아이템", "거래", "지금", "요즘",
}

_TRADE_ID = re.compile(r"거래\s*(?:번호\s*)?(\d+)\s*번?|(?:^|\s)(\d{3,})\s*번")

_FAQ_RESPONSES: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"안녕|반가|ㅎㅇ|하이"),
        "안녕하세요! 게임 아이템 거래소 도우미입니다. "
        "아이템 검색, 시세 확인, 이상거래 점검을 도와드립니다.",
    ),
    (
        re.compile(r"고마워|감사|ㄱㅅ"),
        "도움이 되었다니 다행입니다. 더 필요한 게 있으면 말씀해 주세요.",
    ),
    (
        re.compile(r"수수료"),
        "거래 수수료는 체결가의 5%이며 판매자에게 부과됩니다. "
        "경매 낙찰의 경우도 동일합니다.",
    ),
    (
        re.compile(r"환불"),
        "아이템 인도가 완료되기 전까지는 거래 취소 및 환불이 가능합니다. "
        "인도 완료 후에는 이상거래로 판정된 경우에만 환불이 검토됩니다.",
    ),
    (
        re.compile(r"뭐 ?하는|누구|어떤 ?서비스|사용법|이용 ?방법|어떻게 ?(써|쓰나|이용)"),
        "여러 게임사의 아이템을 한곳에서 거래하는 marketplace입니다. "
        "아이템 검색, 시세 예측, 이상거래 탐지를 제공합니다.",
    ),
]

_DEFAULT_FAQ = (
    "아이템 검색, 시세 확인, 이상거래 점검을 도와드릴 수 있습니다. "
    "찾으시는 아이템이나 확인하고 싶은 거래를 알려주세요."
)

# 검색 설명 프롬프트는 **삭제됐다** (ADR-0036). 남겨두면 "되살리면 되지"로
# 읽히는데, 되살릴 값어치가 없다는 게 그 결정의 내용이다. 경위는 ADR 참고.

# ADR-0038 로 교체됐다. 이전 판본은 `cold_start가 true면…` 이라고 **필드 이름을
# 읽혔고**, 그래서 모델이 `cold_start가 false이므로` 를 사용자 문장에 그대로 냈다.
# 조건 분기는 이제 `_forecast_branch` 가 코드로 한다 — 모델이 그 필드를 볼 이유가
# 없어진다. 측정: `scripts/evaluate_explanation_prompts.py`.
_FORECAST_PROMPT = """다음은 아이템 시세 예측 결과입니다. 사용자에게 2~3문장으로 설명하세요.

- 아래 결과는 내부 데이터입니다. **필드 이름(cold_start, baseline_price 등)을
  답변에 쓰지 마세요.** 사용자는 그 구조를 모릅니다.
- 기준가는 {baseline_source}입니다. 판매자가 정한 등록가와 혼동하지 마세요.
- {conditional}
- 완결된 문장으로 끝내세요.

질의: {query}
결과: {result}"""

# ADR-0038 로 교체됐다. 이전 판본의 마지막 지시가 `…별개라는 점.` 이라는
# **명사형 조각**이었고, 모델은 그걸 글자 하나까지 그대로 옮겨 붙였다 — 화면에서
# 문장이 잘린 것처럼 보였지만 잘린 게 아니라 **지시문이 그 모양**이었다.
_ANOMALY_PROMPT = """다음은 거래 이상 여부 판정 결과입니다. 2~3문장으로 설명하세요.
가장 큰 기여 요인을 근거로 들어 설명하세요.

- 아래 결과는 내부 데이터입니다. **필드 이름(contributions, is_anomaly 등)을
  답변에 쓰지 마세요.**
- **마지막에 다음 내용을 완결된 한 문장으로 덧붙이세요**: 이 판정은 합성 데모
  거래 데이터를 대상으로 한 것이며, 사용자의 실제 거래 번호와는 체계가 다릅니다.

질의: {query}
결과: {result}"""


async def ask(
    es: AsyncElasticsearch,
    llm_client: LLMClient,
    tenant_code: str,
    query: str,
    use_cache: bool = True,
) -> dict[str, Any]:
    settings = get_settings()
    timings: dict[str, float] = {}
    cache = get_semantic_cache()
    embedding: list[float] | None = None

    async def embed() -> list[float]:
        """임베딩을 **필요할 때만** 계산한다 (ADR-0026).

        정확 일치 캐시 적중은 질의 해시만 쓰므로 부를 이유가 없다. 계산이 필요한
        경우에도 전용 스레드로 내보내므로 루프를 막지 않는다(ADR-0028) — 그래서
        `async`다.

        결과를 기억해두는 이유는 유사도 조회와 저장이 같은 값을 쓰기 때문이다 —
        두 번 계산하면 스레드를 두 번 점유한다.
        """
        nonlocal embedding
        if embedding is None:
            started = time.perf_counter()
            embedding = await run_cpu(get_embedding_service().encode_one, query)
            timings["cache_encode_ms"] = _ms(started)
        return embedding

    # --- 1. 캐시 (모든 분기 이전) ---------------------------------------
    if use_cache and settings.semantic_cache_enabled:
        started = time.perf_counter()
        # 임베딩을 **값이 아니라 콜러블로** 넘긴다. 정확 일치면 lookup이 부르지
        # 않으므로 적중 경로에서 `cache_encode_ms` 키 자체가 생기지 않는다 —
        # 그게 이 최적화가 실제로 걸렸다는 런타임 증거다(ADR-0026).
        try:
            lookup_started = time.perf_counter()
            hit = await cache.lookup(tenant_code, query, embed)
            timings["cache_lookup_ms"] = _ms(lookup_started)
        except Exception:
            # 캐시 장애가 요청 실패로 번지면 안 된다. **다만 조용히 넘기지는
            # 않는다** — 이 자리의 `pass` 하나 때문에 배포에서 Redis 인증이
            # 깨진 걸 아무도 몰랐다. 증상은 "적중률 0"뿐이고 그건 캐시가
            # 원래 못 맞히는 것과 구분되지 않는다. 리미터가 같은 상황에서
            # 경고를 남기는 것과 같은 이유다(core/rate_limit.py).
            logger.warning("캐시 조회 실패 — 미적중으로 진행한다", exc_info=True)
            hit = None
        timings["cache_ms"] = _ms(started)
        if hit:
            cached = {
                **hit["response"],
                # 캐시된 응답에는 원래 호출 수가 박혀 있다. 그대로 돌려주면
                # 캐시가 아낀 비용이 아니라 원본 비용으로 읽혀서, 캐시 효과를
                # 측정할 때 정반대의 결론이 나온다.
                "llm_calls": 0,
                "cache": {
                    "hit": True,
                    "match_type": hit["match_type"],
                    "similarity": hit["similarity"],
                    "cached_query": hit["cached_query"],
                },
                "timings": timings,
            }
            record_response(tenant_code, cached)
            return cached

    # --- 2. 라우팅 -------------------------------------------------------
    started = time.perf_counter()
    # 룰이 기권하면 KoELECTRA가 돌고, 그건 동기 CPU 호출이다(격리 median
    # 17~35ms). 룰에서 확정되는 경우는 순수 정규식이라 스레드 왕복(0.21ms)이
    # 손해지만, 어느 쪽으로 갈지는 불러봐야 알므로 호출 단위로 내보낸다.
    decision = await run_cpu(route, query)
    timings["routing_ms"] = _ms(started)
    intent: Intent = decision["intent"]

    # --- 3. 분기 실행 ----------------------------------------------------
    started = time.perf_counter()
    payload, intent = await _execute(es, llm_client, tenant_code, query, intent)
    timings["execution_ms"] = _ms(started)
    # **분기 내부 계측을 위로 올린다.** 이게 없으면 통합 진입점에서 execution_ms
    # 하나만 보이고 그 안이 LLM인지 ES인지 리랭커인지 알 수 없다 — 하위
    # 파이프라인이 이미 재고 있는데도 여기서 버려지고 있었다.
    timings.update(payload.pop("timings", {}))

    response = {
        "query": query,
        "intent": intent.value,
        "routing": {
            "decided_by": decision["decided_by"],
            "confidence": decision.get("confidence"),
            # 분기가 실행 불가능해서 올라간 경우 최초 판정과 달라진다.
            "initial_intent": decision["intent"].value,
        },
        **payload,
    }

    # --- 4. 캐시 저장 ----------------------------------------------------
    # 응답까지 넘긴다 — 0건 응답은 의도가 캐시 가능이어도 저장하지 않는다.
    if (
        use_cache
        and settings.semantic_cache_enabled
        and is_cacheable(intent, response)
    ):
        try:
            # embed()가 결과를 기억하므로, 유사도 조회에서 이미 계산했다면
            # 다시 계산하지 않는다. 여기까지 왔다는 건 캐시 미적중이라 어차피
            # 한 번은 계산해야 한다.
            await cache.store(
                tenant_code=tenant_code,
                query=query,
                embedding=await embed(),
                response=response,
                intent=intent.value,
                ttl=ttl_seconds(intent),
            )
        except Exception:
            # 응답은 이미 만들어졌으므로 실패해도 그대로 내보낸다.
            # 저장이 계속 실패하면 조회는 영원히 미적중이므로, 조회 쪽과
            # 같은 이유로 남긴다.
            logger.warning("캐시 저장 실패 — 응답은 그대로 내보낸다", exc_info=True)

    response["cache"] = {"hit": False}
    response["timings"] = timings
    record_response(tenant_code, response)
    return response


async def _execute(
    es: AsyncElasticsearch,
    llm_client: LLMClient,
    tenant_code: str,
    query: str,
    intent: Intent,
) -> tuple[dict[str, Any], Intent]:
    """분기 실행. 실행 불가능하면 COMPOUND로 올리고 바뀐 의도를 같이 반환."""
    if intent is Intent.FAQ_SMALLTALK:
        return {"answer": _faq_answer(query), "llm_calls": 0}, intent

    if intent is Intent.ITEM_SEARCH:
        result = await run_search(
            es=es, llm_client=llm_client, tenant_code=tenant_code, query=query, size=5
        )
        if not result["results"]:
            return {**_no_results(result["filters"]), "timings": result["timings"]}, intent
        # 0건과 **같은 방식**으로 확정 응답을 만든다 (ADR-0036). 예전에는 여기서
        # 설명 LLM 을 한 번 더 불렀는데, 그 호출이 실제로 만든 건 환각이었다.
        return (
            {
                "answer": _search_answer(result["filters"], len(result["results"])),
                "results": result["results"],
                "llm_calls": 1,
                "timings": result["timings"],
            },
            intent,
        )

    if intent is Intent.PRICE_FORECAST:
        if not _has_target(query):
            return await _agent_branch(llm_client, tenant_code, query)
        return await _forecast_branch(es, llm_client, tenant_code, query)

    if intent is Intent.ANOMALY_CHECK:
        trade_id = _extract_trade_id(query)
        if trade_id is None:
            return await _agent_branch(llm_client, tenant_code, query)
        # 자연어에서 뽑은 번호가 어느 평면인지 알 길이 없다. 합성 코퍼스로
        # 해석하되 그 사실을 답변에 밝히게 한다 — 사용자가 자기 거래 번호를
        # 말한 것일 수도 있고, 두 id 범위는 겹친다.
        # 점수 계산 자체는 0.31ms지만, 이 함수는 첫 호출에서 `build_timeline()`을
        # 태운다 — 26,702건 합성 코퍼스 구축에 **실측 2.3~3.4초**다. 그동안 루프
        # 전체가 멈춘다. `/api/anomaly/*`는 `def` 핸들러라 FastAPI가 이미 스레드로
        # 넘기고 있어서, 이 경로만 노출돼 있었다(ADR-0028).
        result = await run_cpu(detect_trade, tenant_code, trade_id, IdSpace.SYNTHETIC)
        answer, explain_ms = await _timed_complete(
            llm_client, _ANOMALY_PROMPT.format(query=query, result=result)
        )
        return (
            {
                "answer": answer,
                "detection": result,
                "llm_calls": 1,
                "timings": {**result["timings"], "explain_ms": explain_ms},
            },
            intent,
        )

    return await _agent_branch(llm_client, tenant_code, query)


async def _forecast_branch(
    es: AsyncElasticsearch,
    llm_client: LLMClient,
    tenant_code: str,
    query: str,
) -> tuple[dict[str, Any], Intent]:
    """검색으로 아이템을 특정한 뒤 예측. 못 찾으면 에이전트로 올린다."""
    found = await run_search(
        es=es, llm_client=llm_client, tenant_code=tenant_code, query=query, size=1
    )
    if not found["results"]:
        return await _agent_branch(llm_client, tenant_code, query)

    item = found["results"][0]
    result = await forecast_price(
        es=es, tenant_code=tenant_code, item_id=item["item_id"]
    )
    # **콜드스타트 분기를 코드가 한다** (ADR-0038). 프롬프트가 "cold_start가
    # true면"이라고 조건을 걸면 모델이 그 필드를 읽어야 하고, 읽은 이름은
    # 문장으로 새어나온다. 여기서 미리 갈라 완성된 지시문을 넘긴다.
    cold_start = result["cold_start"]
    answer, explain_ms = await _timed_complete(
        llm_client,
        _FORECAST_PROMPT.format(
            query=query,
            result=result,
            baseline_source=(
                "거래 이력 부족 상태의 추정 기준가" if cold_start else "최근 체결가"
            ),
            conditional=(
                "이 아이템은 거래 이력이 부족해 비슷한 아이템들의 추세를 빌려 "
                "추정한 값입니다. 그 점을 반드시 밝히세요."
                if cold_start
                else "이 아이템은 거래 이력이 충분합니다. 추정이라는 언급은 하지 마세요."
            ),
        ),
    )
    return (
        {
            "answer": answer,
            "forecast": result,
            # **검색 결과 항목을 통째로 넘긴다.** 예전엔 id·이름만 줬는데,
            # 그러면 화면이 검색 결과 카드와 같은 모양을 만들 수 없어 별도
            # 표시를 만들게 된다 — 같은 아이템이 화면마다 다르게 보인다.
            "resolved_item": item,
            "llm_calls": 2,
            # 아이템 특정용 검색 + 예측 + 설명. 키가 겹치지 않아 병합이 안전하다.
            "timings": {
                **found["timings"],
                **result["timings"],
                "explain_ms": explain_ms,
            },
        },
        Intent.PRICE_FORECAST,
    )


async def _agent_branch(
    llm_client: LLMClient, tenant_code: str, query: str
) -> tuple[dict[str, Any], Intent]:
    result = await run_agent(llm_client, tenant_code, query)
    return (
        {
            "answer": result["answer"],
            # MCP 도구는 `listing_price` 로 주는데(모델이 기준가와 섞지 않게 한
            # 이름이다) 화면은 검색 결과와 같은 `price` 를 기대한다. 여기서
            # 한 번 맞춰준다 — 두 이름이 각자 있어야 할 이유가 서로 다르다.
            "resolved_item": _as_result_item(result.get("resolved_item")),
            "tool_calls": result["tool_calls"],
            "tool_failures": result["tool_failures"],
            "stop_reason": result["stop_reason"],
            "llm_calls": len(result["tool_calls"]) + 1,
            "timings": result["timings"],
        },
        Intent.COMPOUND,
    )


async def _timed_complete(llm_client: LLMClient, prompt: str) -> tuple[str, float]:
    """설명 생성 LLM 호출 + 소요 시간.

    검색 분기는 LLM을 2회 부르는데 재작성(`query_understanding_ms`)만 분해돼
    있고 설명 생성은 `execution_ms`에 묻혀 있었다. 부하테스트에서 "느린 게
    LLM인가"를 답하려면 두 호출이 다 보여야 한다.
    """
    started = time.perf_counter()
    answer = await llm_client.complete(prompt)
    return answer, _ms(started)


_SALE_TYPE_LABELS = {"FIXED_PRICE": "즉시구매", "AUCTION": "경매"}


def _no_results(filters: dict[str, Any]) -> dict[str, Any]:
    """검색 결과 0건 — LLM을 거치지 않고 확정 응답을 만든다.

    이전에는 빈 결과를 그대로 LLM에 넘겼고, LLM이 알아서 "결과가 없습니다"라고
    답했다. 답은 맞았지만 **구조적 보장이 아니다** — 없는 항목을 지어내지
    않는다는 근거가 프롬프트 한 줄뿐이고, 결과가 없다는 걸 이미 아는 상태에서
    설명 호출을 한 번 더 쓴다.

    `llm_calls`가 0이 아니라 **1**인 점에 주의. `run_search` 안의
    `understand_query`가 이미 한 번 호출됐고 그건 건너뛸 수 없다 — 어떤 필터가
    걸렸는지 알아야 0건 판정이 성립하기 때문이다.

    **ADR-0036 이후로는 결과가 있는 경로도 1이다.** 이 함수가 처음 생겼을 때는
    "0건만 2 → 1"이 요점이었는데, 이제 검색 분기 전체가 1이라 그 대비가 없다.
    즉 `llm_calls`로는 0건 여부를 알 수 없다 — `no_results` 플래그를 봐야 한다.
    """
    conditions = _describe_filters(filters)
    if conditions:
        answer = (
            f"{' · '.join(conditions)} 조건에 맞는 매물이 없습니다. "
            "조건을 완화하면 결과가 나올 수 있습니다."
        )
    else:
        # 필터가 하나도 안 걸렸는데 0건이면 키워드/의미 검색 양쪽이 비었다는
        # 뜻이다. 완화할 조건이 없으니 다른 안내를 한다.
        answer = "검색 결과가 없습니다. 다른 표현으로 다시 검색해 보세요."

    return {
        "answer": answer,
        "results": [],
        # 캐시 정책이 이 플래그로 저장을 막는다(policy.is_cacheable).
        "no_results": True,
        # **0건일 때 필터를 노출하는 이유**: 결과가 있으면 항목 자체가 종류·속성을
        # 달고 나와서 사용자가 판정을 검증할 수 있는데(ADR-0014·0015), 0건에는
        # 검증할 대상이 없다. 그때 유일한 근거가 "무슨 조건으로 찾았는가"다.
        # 질의 재작성이 아직 비결정적이라 오추출("불속성"을 냉기로 뽑는 등)과
        # 진짜 부재를 구분할 수단이 특히 필요하다.
        "applied_filters": filters,
        "conditions": conditions,
        "llm_calls": 1,
    }


def _describe_filters(filters: dict[str, Any]) -> list[str]:
    """추출된 필터 → 사람이 읽는 조건 문구."""
    parts: list[str] = []

    # subcategory가 있으면 category는 생략한다 — `검`이 이미 `무기`를 함의한다.
    if filters.get("subcategory"):
        parts.append(str(filters["subcategory"]))
    elif filters.get("category"):
        parts.append(str(filters["category"]))

    if element := filters.get("element"):
        parts.append(str(element) if element == "무속성" else f"{element} 속성")

    for key, template in (
        ("enhancement_min", "+{} 이상"),
        ("enhancement_max", "+{} 이하"),
        ("level_min", "{}렙 이상"),
        ("level_max", "{}렙 이하"),
    ):
        if (value := filters.get(key)) is not None:
            parts.append(template.format(int(value)))

    for key, template in (("price_min", "{:,}원 이상"), ("price_max", "{:,}원 이하")):
        if (value := filters.get(key)) is not None:
            parts.append(template.format(int(value)))

    if sale_type := filters.get("sale_type"):
        parts.append(_SALE_TYPE_LABELS.get(sale_type, str(sale_type)))

    return parts


def _has_target(query: str) -> bool:
    """질의가 대상을 지칭하는가.

    `"이거 얼마?"`, `"가격 어때"` 처럼 지시대명사와 평가어만 있는 질의는
    시세 분기를 태울 수 없다 — 어느 아이템인지 특정할 수 없기 때문이다.
    """
    tokens = [token.strip("?!.,~") for token in query.split()]
    return any(
        len(token) >= 2 and token not in _STOPWORDS for token in tokens if token
    )


def _extract_trade_id(query: str) -> int | None:
    match = _TRADE_ID.search(query)
    if not match:
        return None
    return int(next(group for group in match.groups() if group))


def _faq_answer(query: str) -> str:
    for pattern, answer in _FAQ_RESPONSES:
        if pattern.search(query):
            return answer
    return _DEFAULT_FAQ


def _search_answer(filters: dict[str, Any], count: int) -> str:
    """검색 결과 있음 — 0건과 같은 방식으로 확정 문장을 만든다 (ADR-0036).

    `_no_results`와 **같은 `_describe_filters`를 쓴다.** 두 경로가 조건을 다르게
    부르면 사용자는 같은 검색이 상황에 따라 다른 말을 한다고 읽는다.

    결과 목록을 문장으로 다시 옮기지 않는 이유: 항목 자체가 이름·가격·종류·속성을
    달고 나오므로(ADR-0014·0015) 설명이 더할 정보가 없다. 그런데도 LLM에게
    설명시키면 **더할 게 없는 자리에서 지어낸다** — 실제로 `10만원 이하 검`
    질의에서 22,000원짜리 검 4건을 받아놓고 "10만원 이하의 검은 없습니다"라고
    답했다.
    """
    conditions = _describe_filters(filters)
    if conditions:
        return f"{' · '.join(conditions)} 조건으로 {count}건 찾았습니다."
    return f"검색 결과 {count}건입니다."


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


def _as_result_item(trimmed: dict[str, Any] | None) -> dict[str, Any] | None:
    """MCP `_trim_item` 모양 → 검색 결과 항목 모양.

    화면이 두 분기의 아이템을 **같은 카드로** 그리게 하려면 필드 이름이 같아야
    한다. 다른 것은 `listing_price` 하나뿐이라 그것만 바꾼다 — MCP 쪽 이름을
    `price` 로 되돌리면 모델이 다시 예측 기준가와 섞는다(실제로 겪었다).
    """
    if not trimmed:
        return None
    item = {key: value for key, value in trimmed.items() if key != "listing_price"}
    item["price"] = trimmed.get("listing_price")
    return item
