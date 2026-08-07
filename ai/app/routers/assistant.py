import logging
from typing import Any

from elasticsearch import AsyncElasticsearch
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.auth import Actor
from app.core.rate_limit import limit_assistant
from app.services.anomaly.exceptions import AnomalyModelNotTrainedError
from app.services.assistant.pipeline import ask
from app.services.forecast.exceptions import ForecastModelNotTrainedError
from app.services.llm.base import LLMClient
from app.services.llm.dependencies import get_llm_client
from app.services.search.es_client import get_es_client
from app.services.search.exceptions import TenantIndexNotFoundError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/assistant", tags=["assistant"])


class AssistantRequest(BaseModel):
    # tenant_code는 토큰 클레임에서 온다 (ADR-0023). 캐시 키가 테넌트별로
    # 갈리므로, 본문으로 받으면 남의 테넌트 캐시를 조회하게 만들 수도 있었다.
    #
    # **길이 상한이 비용 방어의 일부다** (ADR-0035). 한도 3계층(토큰·20회/분·
    # 50회/일)은 전부 **요청 수**를 센다 — 요청 하나의 길이가 300배가 되면 하루
    # 예산도 300배가 된다. 실측: 19,800자 질의가 200으로 통과하고 16.4초 걸렸다
    # (정상 질의는 ~50자 / 4.5초).
    #
    # 상한을 500자로 둔 근거는 둘이다.
    #   1. 파이프라인이 그 너머를 **보지도 않는다** — 임베딩은 128토큰,
    #      리랭커는 256토큰에서 자른다. 그 뒤 텍스트는 LLM 요금만 늘린다
    #   2. 데이터셋 질의 547건의 **최댓값이 33자**다(중앙 18, p99 31). 15배 여유다
    query: str = Field(min_length=1, max_length=500)
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
        # **예외 문자열을 클라이언트로 내보내지 않는다** (ADR-0041). 업스트림
        # 메시지에는 ES 인덱스명·쿼리 DSL·내부 호스트가 섞여 나온다. 백엔드는
        # `server.error.include-stacktrace: never` 로 같은 자세를 이미 취하고
        # 있었고, 여기만 안 맞춰져 있었다 — **한쪽만 선언된 설정은 결정이 아니라
        # 누락이다**(`REDIS_PASSWORD` 때와 같은 모양).
        #
        # 대신 서버에는 남긴다. 안 남기면 진단 수단이 같이 사라진다.
        logger.exception("assistant 요청 처리 실패 (tenant=%s)", actor.tenant_code)
        raise HTTPException(status_code=500, detail="요청 처리에 실패했습니다.") from e
