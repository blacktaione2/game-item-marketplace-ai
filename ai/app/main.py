from fastapi import FastAPI, Response

from app.core.config import get_settings
from app.core.metrics import render
# `llm` 라우터는 제거됐다 (ADR-0033). `POST /api/llm/test` 는 Phase 2 의 왕복
# 확인용이었는데 **인증 의존성이 없었다** — 다른 다섯 라우터가 전부
# `Depends(require_actor)` 를 달 때 여기만 빠졌고, nginx 가 `/api/ai/*` 를 그대로
# 넘기므로 공개 배포에서 **무인증·무한도 OpenAI 프록시**가 된다.
from app.routers import anomaly, assistant, forecast, health, search

settings = get_settings()

app = FastAPI(title=settings.service_name)


@app.get("/metrics", include_in_schema=False)
def metrics() -> Response:
    """Prometheus 스크레이프 엔드포인트.

    Prometheus를 상시 띄우지 않아도 쓸모가 있다 — 히스토그램은 누적값이라
    부하테스트 전후로 이 응답을 받아 차분하면 실행 구간의 정확한 집계가 나온다.
    """
    return Response(content=render(), media_type="text/plain; version=0.0.4")

app.include_router(health.router)
app.include_router(search.router)
app.include_router(forecast.router)
app.include_router(anomaly.router)
app.include_router(assistant.router)
