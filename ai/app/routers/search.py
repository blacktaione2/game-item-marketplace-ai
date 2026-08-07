import logging
from typing import Any

from elasticsearch import AsyncElasticsearch
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.auth import Actor, require_actor
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
    actor: Actor = Depends(require_actor),
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
