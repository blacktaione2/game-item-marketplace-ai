"""Claude 구현체 — OpenAI 장애 시 폴백 대상 (ADR-0042).

ADR-0004 가 Claude 를 **"서킷 브레이커 뒤의 진짜 폴백 대상"** 으로 적어둔 그것이다.
정적 안내문이 아니라 실제로 다른 LLM 이 답한다.

## 이 파일이 하는 일의 대부분은 번역이다

`LLMClient` 인터페이스는 중립인데, 그 위에 얹힌 **에이전트 루프가 OpenAI 메시지
형식을 그대로 쓴다** (`{"role": "tool", "tool_call_id": ...}`). 인터페이스만
맞추고 형식을 안 맞추면 `complete()` 는 되고 **에이전트만 조용히 깨진다** — 폴백이
가장 필요한 순간에.

| OpenAI | Anthropic |
|---|---|
| `{"role": "system", ...}` 메시지 | **최상위 `system` 파라미터** |
| `assistant` + `tool_calls[]` | `content` 안의 `tool_use` 블록 |
| `{"role": "tool", "tool_call_id"}` | `user` 메시지 안의 `tool_result` 블록 |
| `{"type": "function", "function": {..., "parameters"}}` | `{name, description, input_schema}` |
| `max_tokens` 선택 | **필수** |

## 도구 결과를 합친다

한 번에 도구를 여러 개 부르면 OpenAI 는 `tool` 메시지가 여러 개 이어진다.
Anthropic 은 그걸 **한 `user` 메시지 안의 블록 여러 개**로 기대한다. 안 합치면
역할이 번갈아 나오지 않아 400 이 난다 — 도구를 하나만 부르는 동안에는 안 보이다가
둘을 부르는 순간 터지는 종류다.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from anthropic import AsyncAnthropic

from app.services.llm.base import ChatResult, LLMClient, ToolCall

logger = logging.getLogger(__name__)

# Anthropic 은 `max_tokens` 가 **필수**다. 이 프로젝트의 응답은 설명 2~3문장이나
# 짧은 JSON 이라 넉넉하다. 에이전트의 최종 답변이 가장 긴데 그것도 한참 아래다.
DEFAULT_MAX_TOKENS = 2048


class AnthropicClient(LLMClient):
    def __init__(
        self,
        api_key: str,
        model: str,
        temperature: float = 0.0,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> None:
        # OpenAIClient 와 같은 이유로 자리표시자를 넣는다 — 생성 시점에 키를
        # 검사하면 FastAPI 의 의존성 해석 중에 터져서 라우터의 예외 처리가
        # 손도 못 댄다. 키가 없으면 실제 호출에서 인증 오류로 자연스럽게 실패한다.
        self._client = AsyncAnthropic(api_key=api_key or "not-configured")
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> ChatResult:
        system, converted = _split_system(messages)

        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": converted,
            "max_tokens": self._max_tokens,
            # OpenAI 쪽과 같은 이유로 **명시적으로** 넘긴다. 생략하면 기본값이
            # 적용되고, 그게 이 프로젝트에서 오래 안 잡힌 비결정성의 원인이었다
            # (ADR-0017). 폴백이 원본보다 흔들리면 장애 때 답이 더 나빠진다.
            "temperature": self._temperature,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = [_to_anthropic_tool(tool) for tool in tools]

        response = await self._client.messages.create(**kwargs)
        return _to_chat_result(response)


def _split_system(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """`system` 메시지를 뽑아내고 나머지를 Anthropic 형식으로 옮긴다.

    **연속된 도구 결과는 한 메시지로 합친다** — 위 모듈 설명 참고.
    """
    system_parts: list[str] = []
    converted: list[dict[str, Any]] = []

    for message in messages:
        role = message.get("role")

        if role == "system":
            system_parts.append(str(message.get("content") or ""))
            continue

        if role == "tool":
            block = {
                "type": "tool_result",
                "tool_use_id": message.get("tool_call_id"),
                "content": str(message.get("content") or ""),
            }
            # 바로 앞이 도구 결과 묶음이면 거기에 붙인다.
            if converted and _is_tool_result_message(converted[-1]):
                converted[-1]["content"].append(block)
            else:
                converted.append({"role": "user", "content": [block]})
            continue

        if role == "assistant":
            blocks: list[dict[str, Any]] = []
            if content := message.get("content"):
                blocks.append({"type": "text", "text": str(content)})
            for call in message.get("tool_calls") or []:
                function = call.get("function", {})
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call.get("id"),
                        "name": function.get("name"),
                        "input": _loads(function.get("arguments")),
                    }
                )
            # 도구 호출만 있고 본문이 없는 assistant 턴이 흔하다. 빈 블록 목록을
            # 보내면 400 이므로 그때는 아예 넣지 않는다.
            if blocks:
                converted.append({"role": "assistant", "content": blocks})
            continue

        converted.append(
            {"role": "user", "content": str(message.get("content") or "")}
        )

    return "\n\n".join(part for part in system_parts if part), converted


def _is_tool_result_message(message: dict[str, Any]) -> bool:
    content = message.get("content")
    return (
        message.get("role") == "user"
        and isinstance(content, list)
        and bool(content)
        and content[0].get("type") == "tool_result"
    )


def _loads(raw: Any) -> dict[str, Any]:
    """도구 인자 문자열 → dict. 깨져 있으면 빈 dict.

    `openai_client._to_tool_call` 과 같은 자세다 — 여기서 터뜨리면 에이전트가
    스스로 고쳐 부를 기회가 사라진다.
    """
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        logger.warning("도구 인자 JSON 파싱 실패: %s", raw)
        return {}


def _to_anthropic_tool(tool: dict[str, Any]) -> dict[str, Any]:
    """OpenAI 도구 스키마 → Anthropic 도구 스키마."""
    function = tool.get("function", tool)
    return {
        "name": function.get("name"),
        "description": function.get("description") or "",
        # 이름만 다르고 내용은 같은 JSON Schema 다.
        "input_schema": function.get("parameters") or {"type": "object", "properties": {}},
    }


def _to_chat_result(response: Any) -> ChatResult:
    """Anthropic 응답 블록 → 중립 `ChatResult`."""
    texts: list[str] = []
    tool_calls: list[ToolCall] = []

    for block in getattr(response, "content", None) or []:
        block_type = getattr(block, "type", None)
        if block_type == "text":
            texts.append(getattr(block, "text", "") or "")
        elif block_type == "tool_use":
            tool_calls.append(
                ToolCall(
                    id=getattr(block, "id", ""),
                    name=getattr(block, "name", ""),
                    arguments=dict(getattr(block, "input", None) or {}),
                )
            )

    return ChatResult(content="".join(texts), tool_calls=tool_calls)
