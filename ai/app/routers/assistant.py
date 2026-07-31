from typing import Any

from elasticsearch import AsyncElasticsearch
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.auth import Actor
from app.core.rate_limit import limit_assistant
from app.services.anomaly.exceptions import AnomalyModelNotTrainedError
from app.services.assistant.pipeline import ask
from app.services.forecast.exceptions import ForecastModelNotTrainedError
from app.services.llm.base import LLMClient
from app.services.llm.dependencies import get_llm_client
from app.services.search.es_client import get_es_client
from app.services.search.exceptions import TenantIndexNotFoundError

router = APIRouter(prefix="/api/assistant", tags=["assistant"])


class AssistantRequest(BaseModel):
    # tenant_code는 토큰 클레임에서 온다 (ADR-0023). 캐시 키가 테넌트별로
    # 갈리므로, 본문으로 받으면 남의 테넌트 캐시를 조회하게 만들 수도 있었다.
    query: str
    # 캐시 효과를 측정하거나 디버깅할 때 끌 수 있게 열어둔다.
    use_cache: bool = True


@router.post("")
async def assistant(
    request: AssistantRequest,
    # limit_assistant가 require_actor를 품고 있다 — 인증을 통과한 뒤에 한도를
    # 센다. 순서가 반대면 인증 실패도 한도를 소모한다.
    actor: Actor = Depends(limit_assistant),
    es: AsyncElasticsearch = Depends(get_es_client),
    llm_client: LLMClient = Depends(get_llm_client),
) -> dict[str, Any]:
    try:
        return await ask(
            es=es,
            llm_client=llm_client,
            tenant_code=actor.tenant_code,
            query=request.query,
            use_cache=request.use_cache,
        )
    except TenantIndexNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except (ForecastModelNotTrainedError, AnomalyModelNotTrainedError) as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"요청 처리 실패: {e}") from e
