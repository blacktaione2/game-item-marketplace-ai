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

import re
import time
from typing import Any

from elasticsearch import AsyncElasticsearch

from app.core.config import get_settings
from app.core.ids import IdSpace
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

_SEARCH_PROMPT = """다음은 사용자 질의에 대한 아이템 검색 결과입니다.
2~3문장으로 간결하게 설명하세요. 질의와 종류가 맞지 않는 항목이 섞여 있으면
그 항목은 빼고 말하세요. 없는 사실을 지어내지 마세요.

질의: {query}
결과: {results}"""

_FORECAST_PROMPT = """다음은 아이템 시세 예측 결과입니다. 2~3문장으로 설명하세요.

- baseline_price는 {baseline_source}를 기준으로 한 값입니다. 등록가와 혼동하지 마세요.
- cold_start가 true면 실제 거래 이력이 부족해 유사 아이템 추세를 물려받은
  추정치라는 점을 반드시 밝히세요.

질의: {query}
결과: {result}"""

_ANOMALY_PROMPT = """다음은 거래 이상 여부 판정 결과입니다. 2~3문장으로 설명하세요.
contributions는 이상 점수에 대한 피처별 기여도입니다. 가장 큰 기여 요인을
근거로 들어 설명하세요.

**반드시 한 문장으로 덧붙이세요**: 이 판정은 합성 데모 거래 데이터를 대상으로
하며, 사용자의 실제 거래 내역과는 번호 체계가 별개라는 점.

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

    # --- 1. 캐시 (모든 분기 이전) ---------------------------------------
    if use_cache and settings.semantic_cache_enabled:
        started = time.perf_counter()
        try:
            embedding = get_embedding_service().encode_one(query)
            hit = await cache.lookup(tenant_code, query, embedding)
        except Exception:
            hit = None  # 캐시 장애가 요청 실패로 번지면 안 된다
        timings["cache_ms"] = _ms(started)
        if hit:
            return {
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

    # --- 2. 라우팅 -------------------------------------------------------
    started = time.perf_counter()
    decision = route(query)
    timings["routing_ms"] = _ms(started)
    intent: Intent = decision["intent"]

    # --- 3. 분기 실행 ----------------------------------------------------
    started = time.perf_counter()
    payload, intent = await _execute(es, llm_client, tenant_code, query, intent)
    timings["execution_ms"] = _ms(started)

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
            if embedding is None:
                embedding = get_embedding_service().encode_one(query)
            await cache.store(
                tenant_code=tenant_code,
                query=query,
                embedding=embedding,
                response=response,
                intent=intent.value,
                ttl=ttl_seconds(intent),
            )
        except Exception:
            pass  # 저장 실패는 조용히 넘긴다 — 응답은 이미 만들어졌다

    response["cache"] = {"hit": False}
    response["timings"] = timings
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
            return _no_results(result["filters"]), intent
        answer = await llm_client.complete(
            _SEARCH_PROMPT.format(query=query, results=_brief(result["results"]))
        )
        return {"answer": answer, "results": result["results"], "llm_calls": 2}, intent

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
        result = detect_trade(tenant_code, trade_id, IdSpace.SYNTHETIC)
        answer = await llm_client.complete(
            _ANOMALY_PROMPT.format(query=query, result=result)
        )
        return {"answer": answer, "detection": result, "llm_calls": 1}, intent

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
    answer = await llm_client.complete(
        _FORECAST_PROMPT.format(
            query=query,
            result=result,
            baseline_source=(
                "최근 체결가" if not result["cold_start"] else "거래 이력 부족 상태의 추정 기준가"
            ),
        )
    )
    return (
        {
            "answer": answer,
            "forecast": result,
            "resolved_item": {"item_id": item["item_id"], "name": item["name"]},
            "llm_calls": 2,
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
            "tool_calls": result["tool_calls"],
            "tool_failures": result["tool_failures"],
            "stop_reason": result["stop_reason"],
            "llm_calls": len(result["tool_calls"]) + 1,
        },
        Intent.COMPOUND,
    )


_SALE_TYPE_LABELS = {"FIXED_PRICE": "즉시구매", "AUCTION": "경매"}


def _no_results(filters: dict[str, Any]) -> dict[str, Any]:
    """검색 결과 0건 — LLM을 거치지 않고 확정 응답을 만든다.

    이전에는 빈 결과를 그대로 LLM에 넘겼고, LLM이 알아서 "결과가 없습니다"라고
    답했다. 답은 맞았지만 **구조적 보장이 아니다** — 없는 항목을 지어내지
    않는다는 근거가 프롬프트 한 줄뿐이고, 결과가 없다는 걸 이미 아는 상태에서
    설명 호출을 한 번 더 쓴다.

    `llm_calls`가 0이 아니라 **1**인 점에 주의. `run_search` 안의
    `understand_query`가 이미 한 번 호출됐고 그건 건너뛸 수 없다 — 어떤 필터가
    걸렸는지 알아야 0건 판정이 성립하기 때문이다. 없어지는 건 설명 생성 호출
    하나다(2 → 1).
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


def _brief(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "name": doc["name"],
            "category": doc["category"],
            "price": doc["price"],
            "enhancement_level": doc["enhancement_level"],
            "required_level": doc["required_level"],
        }
        for doc in results
    ]


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)
