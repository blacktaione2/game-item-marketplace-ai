from fastapi import FastAPI

from app.core.config import get_settings
from app.routers import anomaly, assistant, forecast, health, llm, search

settings = get_settings()

app = FastAPI(title=settings.service_name)

app.include_router(health.router)
app.include_router(llm.router)
app.include_router(search.router)
app.include_router(forecast.router)
app.include_router(anomaly.router)
app.include_router(assistant.router)
