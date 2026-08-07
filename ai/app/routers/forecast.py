import logging
from typing import Any

from elasticsearch import AsyncElasticsearch
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.auth import Actor, require_actor
from app.services.forecast.exceptions import (
    ForecastModelNotTrainedError,
    InsufficientHistoryError,
    ItemNotFoundError,
)
from app.services.forecast.pipeline import forecast_price
from app.services.search.es_client import get_es_client
from app.services.search.exceptions import TenantIndexNotFoundError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/forecast", tags=["forecast"])


class ForecastRequest(BaseModel):
    # tenant_code는 토큰 클레임에서 온다 (ADR-0023).
    item_id: int
    # 상한은 학습된 모델의 horizon이라 여기서는 느슨하게 두고 파이프라인에서
    # 실제 값과 비교해 거른다.
    horizon: int | None = Field(default=None, ge=1, le=30)


@router.post("")
async def forecast(
    request: ForecastRequest,
    actor: Actor = Depends(require_actor),
    es: AsyncElasticsearch = Depends(get_es_client),
) -> dict[str, Any]:
    try:
        return await forecast_price(
            es=es,
            tenant_code=actor.tenant_code,
            item_id=request.item_id,
            horizon=request.horizon,
        )
    except TenantIndexNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ItemNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ForecastModelNotTrainedError as e:
        # 설정 누락이지 요청 잘못이 아니므로 503.
        raise HTTPException(status_code=503, detail=str(e)) from e
    except InsufficientHistoryError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        # 예외 문자열을 클라이언트로 내보내지 않는다 (ADR-0041). 서버에는 남긴다.
        logger.exception("forecast 실패")
        raise HTTPException(
            status_code=500, detail="시세 예측에 실패했습니다."
        ) from e
