from functools import lru_cache

from app.core.config import get_settings
from app.services.llm.base import LLMClient
from app.services.llm.openai_client import OpenAIClient


@lru_cache
def get_llm_client() -> LLMClient:
    settings = get_settings()
    return OpenAIClient(
        api_key=settings.openai_api_key,
        model=settings.openai_model,
        temperature=settings.openai_temperature,
    )
