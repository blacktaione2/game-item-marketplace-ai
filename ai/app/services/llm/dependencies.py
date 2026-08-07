import logging
from functools import lru_cache

from app.core.config import get_settings
from app.services.llm.anthropic_client import AnthropicClient
from app.services.llm.base import LLMClient
from app.services.llm.fallback import FallbackLLMClient
from app.services.llm.openai_client import OpenAIClient

logger = logging.getLogger(__name__)


@lru_cache
def get_llm_client() -> LLMClient:
    """OpenAI 를 쓰되, 키가 있으면 Claude 폴백으로 감싼다 (ADR-0042).

    **키가 없으면 폴백을 만들지 않는다.** 없는 폴백을 있는 척 감싸면 장애가 나서야
    "폴백이 없었다" 를 알게 된다. 어느 쪽이든 기동 시점에 로그로 남는다 — 이
    저장소가 `REDIS_PASSWORD` 에서 배운 것이다: **조용한 기본값은 로테이션 전까지
    안 들킨다.**
    """
    settings = get_settings()
    primary = OpenAIClient(
        api_key=settings.openai_api_key,
        model=settings.openai_model,
        temperature=settings.openai_temperature,
    )

    if not settings.anthropic_api_key:
        logger.info(
            "LLM 폴백 없음 — ANTHROPIC_API_KEY 가 비어 있다 (ai/.env 를 확인할 것. "
            "저장소 루트 .env 는 docker-compose 용이라 앱이 읽지 않는다)"
        )
        return primary

    logger.info("LLM 폴백 활성 — %s", settings.anthropic_model)
    return FallbackLLMClient(
        primary=primary,
        secondary=AnthropicClient(
            api_key=settings.anthropic_api_key,
            model=settings.anthropic_model,
            # **1차와 같은 값을 쓴다.** 폴백이 더 흔들리면 장애 때 답이 더
            # 나빠진다 — 그건 폴백의 목적과 정반대다.
            temperature=settings.openai_temperature,
        ),
        failure_threshold=settings.llm_failure_threshold,
        reset_seconds=settings.llm_circuit_reset_seconds,
    )
