from fastapi import FastAPI, Response

from app.core.config import get_settings
from app.core.metrics import render
from app.routers import anomaly, assistant, forecast, health, llm, search

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
app.include_router(llm.router)
app.include_router(search.router)
app.include_router(forecast.router)
app.include_router(anomaly.router)
app.include_router(assistant.router)
