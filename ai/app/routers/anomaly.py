from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.ids import IdSpace, UnsupportedIdSpaceError
from app.services.anomaly.exceptions import (
    AnomalyModelNotTrainedError,
    TradeNotFoundError,
    UnknownTenantError,
)
from app.services.anomaly.pipeline import detect_trade, list_alerts

router = APIRouter(prefix="/api/anomaly", tags=["anomaly"])


class DetectRequest(BaseModel):
    tenant_code: str
    trade_id: int
    # **기본값을 두지 않는다.** 합성 코퍼스와 백엔드 거래는 id 범위가 겹치므로
    # 호출자가 어느 쪽인지 밝혀야 한다. 기본값을 주면 그 순간 "모르고 넘긴
    # 사람"이 조용히 틀린 답을 받는다.
    id_space: IdSpace


@router.post("/detect")
def detect(request: DetectRequest) -> dict[str, Any]:
    try:
        return detect_trade(request.tenant_code, request.trade_id, request.id_space)
    except UnsupportedIdSpaceError as e:
        # 요청은 이해했지만 그 데이터 평면이 아직 연동되지 않았다.
        raise HTTPException(status_code=501, detail=str(e)) from e
    except TradeNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except UnknownTenantError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except AnomalyModelNotTrainedError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"이상거래 탐지 실패: {e}") from e


@router.get("/alerts")
def alerts(
    tenant_code: str,
    limit: int = Query(default=10, ge=1, le=100),
) -> dict[str, Any]:
    try:
        return list_alerts(tenant_code, limit)
    except UnknownTenantError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except AnomalyModelNotTrainedError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"알림 조회 실패: {e}") from e
