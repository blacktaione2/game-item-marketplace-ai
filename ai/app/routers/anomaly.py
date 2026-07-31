from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.auth import Actor, require_actor, require_admin
from app.core.ids import MalformedIdRefError, UnsupportedIdSpaceError, parse_ref
from app.services.anomaly.exceptions import (
    AnomalyModelNotTrainedError,
    TradeNotFoundError,
    UnknownTenantError,
)
from app.services.anomaly.pipeline import detect_trade, list_alerts

router = APIRouter(prefix="/api/anomaly", tags=["anomaly"])


class DetectRequest(BaseModel):
    # tenant_code는 토큰 클레임에서 온다 (ADR-0023).
    # 참조가 **자기 공간을 들고 온다** — `"syn:3"` / `"pg:3"`.
    # 예전엔 `trade_id: int` + `id_space` 두 필드였는데, 그러면 둘이 어긋나게
    # 보낼 수 있고 어긋난 걸 검출할 방법도 없다. 한 값으로 합치면 그 조합
    # 자체가 사라진다. 접두사 없는 `"3"`은 400이다 — 추측하지 않는다.
    trade_ref: str


@router.post("/detect")
def detect(
    request: DetectRequest, actor: Actor = Depends(require_actor)
) -> dict[str, Any]:
    # 해석 불가(400)와 미연동(501)은 다른 답이다. 전자는 요청을 고치면 되고,
    # 후자는 요청이 맞는데 서버가 아직 못 한다는 뜻이다.
    try:
        id_space, trade_id = parse_ref(request.trade_ref, "거래")
    except MalformedIdRefError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    try:
        return detect_trade(actor.tenant_code, trade_id, id_space)
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
    limit: int = Query(default=10, ge=1, le=100),
    # **GM 전용.** 이상거래 검토 큐는 다른 사용자의 거래 내역·상대방 id를
    # 그대로 보여주므로 일반 사용자가 볼 것이 아니다. 이 프로젝트에서 역할
    # 인가가 실제로 의미를 갖는 유일한 지점이다(ADR-0023).
    actor: Actor = Depends(require_admin),
) -> dict[str, Any]:
    try:
        return list_alerts(actor.tenant_code, limit)
    except UnknownTenantError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except AnomalyModelNotTrainedError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"알림 조회 실패: {e}") from e
