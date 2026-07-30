import json
import logging
from typing import Any

from openai import AsyncOpenAI

from app.services.llm.base import ChatResult, LLMClient, ToolCall

logger = logging.getLogger(__name__)


class OpenAIClient(LLMClient):
    def __init__(self, api_key: str, model: str, temperature: float = 0.0) -> None:
        # AsyncOpenAI() validates the key eagerly at construction time, which
        # would blow up during FastAPI's DI resolution (before the router's
        # try/except runs) if no key is configured yet. Pass a placeholder so
        # construction always succeeds; a missing/invalid key then fails
        # naturally as an auth error on the actual API call, where it's
        # caught and reported cleanly.
        self._client = AsyncOpenAI(api_key=api_key or "not-configured")
        self._model = model
        self._temperature = temperature

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> ChatResult:
        # temperature를 **명시적으로** 넘긴다. 생략하면 API 기본값 1.0이 적용되고,
        # 그게 이 프로젝트에서 오래 안 잡힌 비결정성의 원인이었다(ADR-0017).
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": self._temperature,
        }
        if tools:
            kwargs["tools"] = tools

        response = await self._client.chat.completions.create(**kwargs)
        message = response.choices[0].message
        return ChatResult(
            content=message.content or "",
            tool_calls=[
                call
                for call in (
                    _to_tool_call(raw) for raw in (message.tool_calls or [])
                )
                if call is not None
            ],
        )


def _to_tool_call(raw: Any) -> ToolCall | None:
    function = getattr(raw, "function", None)
    if function is None:
        # function 이외 타입(custom tool 등)은 이 프로젝트에서 쓰지 않는다.
        return None

    try:
        arguments = json.loads(function.arguments or "{}")
    except json.JSONDecodeError:
        # 인자 JSON이 깨진 채로 오는 경우가 드물게 있다. 여기서 터뜨리지 않고
        # 빈 인자로 넘기면 도구 스키마 검증이 실패하고, 그 실패가 에이전트에게
        # 구조화된 에러로 돌아가 스스로 고쳐 부를 기회가 생긴다.
        logger.warning("도구 인자 JSON 파싱 실패: %s", function.arguments)
        arguments = {}

    return ToolCall(id=raw.id, name=function.name, arguments=arguments)
