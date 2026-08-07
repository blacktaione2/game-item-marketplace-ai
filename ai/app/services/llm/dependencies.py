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
        # **`warning` 이다. `info` 로 두면 아무 데도 안 나온다** — 이 앱은 로깅을
        # 설정하지 않으므로 파이썬 루트 기본값 `WARNING` 아래는 전부 버려진다.
        # 실제로 배포 후 `docker logs | grep` 이 빈 결과를 냈고, 그건 "폴백 없음"
        # 과 "로그가 안 보임" 을 구분해주지 못했다.
        #
        # 레벨도 내용상 맞다 — 폴백 없이 도는 것은 정상이 아니라 **약화된 구성**이다.
        logger.warning(
            "LLM 폴백 없음 — ANTHROPIC_API_KEY 가 비어 있다 (ai/.env 를 확인할 것. "
            "저장소 루트 .env 는 docker-compose 용이라 앱이 읽지 않는다)"
        )
        return primary

    # 정상 구성이라 `info` 가 맞다. 대신 **눈으로 확인할 자리는 `/health` 에 있다**
    # (`llm_fallback`) — 로그 레벨에 기대지 않는다.
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
