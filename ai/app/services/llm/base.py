from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field


class ToolCall(BaseModel):
    """LLM이 요청한 도구 호출 1건. 프로바이더 표현을 감춘 중립 형태."""

    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ChatResult(BaseModel):
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)


class LLMClient(ABC):
    """프로바이더 전환이 쉽도록 둔 추상 인터페이스.

    지금은 OpenAIClient만 구현체로 붙어 있다. Claude를 서킷 브레이커
    폴백/LLM Judge 용도로 추가하는 건 스트레치 단계 — 그때도 이 인터페이스
    위에 ClaudeClient만 구현하면 되도록 설계했다. (docs/01-Decisions/0004 참고)

    Phase 6에서 `chat()`을 추상 메서드로 두고 `complete()`를 그 위의 기본
    구현으로 내렸다. 도구 호출은 메시지 배열과 도구 스키마가 필요해서
    문자열 왕복인 `complete()`로는 표현할 수 없기 때문이다. 이렇게 두면
    기존 `complete()` 호출부(query_understanding 등)는 손댈 필요가 없다.
    """

    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> ChatResult:
        """메시지 배열 + (선택) 도구 스키마 → 응답 본문과 도구 호출 목록."""

    async def complete(self, prompt: str) -> str:
        """단일 프롬프트 → 응답 문자열. `chat()` 위의 편의 래퍼."""
        result = await self.chat([{"role": "user", "content": prompt}])
        return result.content
