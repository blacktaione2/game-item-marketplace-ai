from typing import Any

from elasticsearch import AsyncElasticsearch
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.services.anomaly.exceptions import AnomalyModelNotTrainedError
from app.services.assistant.pipeline import ask
from app.services.forecast.exceptions import ForecastModelNotTrainedError
from app.services.llm.base import LLMClient
from app.services.llm.dependencies import get_llm_client
from app.services.search.es_client import get_es_client
from app.services.search.exceptions import TenantIndexNotFoundError

router = APIRouter(prefix="/api/assistant", tags=["assistant"])


class AssistantRequest(BaseModel):
    tenant_code: str
    query: str
    # 캐시 효과를 측정하거나 디버깅할 때 끌 수 있게 열어둔다.
    use_cache: bool = True


@router.post("")
async def assistant(
    request: AssistantRequest,
    es: AsyncElasticsearch = Depends(get_es_client),
    llm_client: LLMClient = Depends(get_llm_client),
) -> dict[str, Any]:
    try:
        return await ask(
            es=es,
            llm_client=llm_client,
            tenant_code=request.tenant_code,
            query=request.query,
            use_cache=request.use_cache,
        )
    except TenantIndexNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except (ForecastModelNotTrainedError, AnomalyModelNotTrainedError) as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"요청 처리 실패: {e}") from e
