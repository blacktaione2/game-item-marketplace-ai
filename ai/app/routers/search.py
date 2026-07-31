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

router = APIRouter(prefix="/api/search", tags=["search"])


class SearchRequest(BaseModel):
    # tenant_code는 **토큰 클레임에서 온다.** 본문에도 두면 같은 사실의 출처가
    # 둘이 되고, 어긋나게 보낼 수 있으면서 어긋난 걸 검출할 방법이 없다 —
    # ADR-0022에서 trade_id + id_space를 한 값으로 합친 것과 같은 이유다.
    query: str
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
        raise HTTPException(status_code=500, detail=f"검색 실패: {e}") from e
