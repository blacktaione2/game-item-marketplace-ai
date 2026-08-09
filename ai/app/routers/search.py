import logging
from typing import Any

from elasticsearch import AsyncElasticsearch
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.auth import Actor
from app.core.rate_limit import consume_daily, limit_assistant
from app.services.llm.base import LLMClient
from app.services.llm.dependencies import get_llm_client
from app.services.search.es_client import get_es_client
from app.services.search.exceptions import TenantIndexNotFoundError
from app.services.search.pipeline import search as run_search

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/search", tags=["search"])


class SearchRequest(BaseModel):
    # tenant_code는 **토큰 클레임에서 온다.** 본문에도 두면 같은 사실의 출처가
    # 둘이 되고, 어긋나게 보낼 수 있으면서 어긋난 걸 검출할 방법이 없다 —
    # ADR-0022에서 trade_id + id_space를 한 값으로 합친 것과 같은 이유다.
    # **`size`는 묶여 있는데 `query`는 안 묶여 있었다** (ADR-0035). 같은 DTO 안에서
    # 크기는 상한을 두고 길이는 빠뜨린 것이라, 그 대비 자체가 이 결함의 발견 단서였다.
    # 상한 근거는 `assistant.py` 참고 — 파이프라인이 128/256토큰에서 자르고,
    # 데이터셋 질의 547건의 최댓값이 33자다.
    query: str = Field(min_length=1, max_length=500)
    size: int = Field(default=10, ge=1, le=50)
    # 리랭킹 전/후 비교를 위해 끌 수 있게 열어둠 (RAGAS 평가에서 사용 예정)
    use_rerank: bool = True


@router.post("")
async def search_items(
    request: SearchRequest,
    # **한도가 여기에도 걸린다.** 예전에는 `require_actor` 만 있었다 —
    # `rate_limit.py` 가 "막을 것은 `/api/assistant` 하나다, 조회 계열은 싸다"
    # 라고 적어둔 대로였는데, **이 경로는 조회 계열이 아니다.** `run_search` 는
    # `understand_query` 와 `judge_in_domain` 을 부르므로 **호출 한 번에 LLM 2회**
    # 나간다. 형제 라우터와 비교하면 바로 보인다: `/api/forecast` 와
    # `/api/anomaly/detect` 는 `llm_client` 를 애초에 주입받지 않는다.
    #
    # 그래서 토큰만 있으면 20회/분·50회/일을 전부 우회해 OpenAI 를 무한정 부를 수
    # 있었다 — ADR-0033 이 `/api/llm/test` 에서 막은 것과 같은 종류의 구멍이고,
    # 그때 세운 규칙("경로마다 개별로 붙이면 빠뜨려도 신호가 없다")이 인증에만
    # 적용되고 한도에는 적용되지 않은 결과다.
    # `tests/test_llm_route_metering.py` 가 **열거가 아니라 유도로** 고정한다.
    actor: Actor = Depends(limit_assistant),
    es: AsyncElasticsearch = Depends(get_es_client),
    llm_client: LLMClient = Depends(get_llm_client),
) -> dict[str, Any]:
    try:
        return await run_search(
            es=es,
            llm_client=llm_client,
            tenant_code=actor.tenant_code,
            query=request.query,
            size=request.size,
            use_rerank=request.use_rerank,
        )
    except TenantIndexNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        # 예외 문자열을 클라이언트로 내보내지 않는다 (ADR-0041). 서버에는 남긴다.
        logger.exception("search 실패")
        raise HTTPException(
            status_code=500, detail="검색에 실패했습니다."
        ) from e
    finally:
        # **나가는 길을 세지 않고 `finally` 에 둔다** (ADR-0046). 실패도 취소도
        # 소비한다 — 여기까지 왔으면 LLM 호출이 이미 나갔을 수 있고, 공짜로
        # 끝나는 경로를 하나라도 남기면 그게 곧 우회로다.
        #
        # `_ask_metered` 와 달리 **조건 없이 소비한다.** 이 경로에는 시맨틱
        # 캐시가 없어서(캐시는 `assistant` 파이프라인에 있다) 비용 0인 적중이
        # 애초에 없다.
        await consume_daily(actor)
