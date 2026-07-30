"""Agentic Tool-Calling 루프.

복합 질의("이 검 적정가고 사기 아니야?")는 도구 하나로 답할 수 없다. 검색으로
아이템을 특정하고 → 그 id로 시세를 예측하고 → 필요하면 거래를 점검해야 한다.
이 순차 체인을 LLM이 스스로 짜게 한다.

## 범위

**순차 멀티스텝만** 지원한다(최대 `agent_max_steps`). 병렬 도구 호출은
스트레치 — 임베딩 동기 호출이 이벤트 루프를 막는 문제가 미해결이라 병렬로
띄워도 실제 이득이 안 난다.

조건부 Reflection도 스트레치다. "확신도가 낮다"를 무엇으로 판정할지 자체가
설계·검증 대상인데, 에이전트가 실제로 도는 걸 본 뒤에 정하는 게 근거가 있다.

## 도구 실패

도구가 실패해도 **예외를 던지지 않고** 에러 내용을 모델에게 결과로 돌려준다.
모델이 다른 도구로 우회하거나 부분 결과로 답할 수 있다. 시스템 프롬프트에
"실패한 것은 숨기지 말고 밝혀라"를 명시해서, 조용히 빠뜨리는 대신 한계를
드러내게 한다.
"""

from __future__ import annotations

import json
import time
from typing import Any

from app.core.config import get_settings
from app.services.llm.base import LLMClient
from app.services.mcp.session import (
    call_tool_text,
    openai_tool_schemas,
    tool_session,
)

_SYSTEM_PROMPT = """당신은 게임 아이템 거래소의 상담 도우미입니다.

주어진 도구로 사실을 확인한 뒤에 답하세요. 추측으로 가격이나 판정을 말하지 마세요.

규칙:
- 모든 도구 호출에 tenant_code="{tenant_code}" 를 넣으세요.
- 아이템 id나 거래 id를 모르면 먼저 search_items로 찾으세요.
- **시세·적정가·가격 전망을 물으면 반드시 forecast_item_price까지 호출하세요.**
  search_items의 listing_price는 판매자가 올린 등록가일 뿐 시세가 아닙니다.
- **등록가(listing_price)와 예측 기준가(baseline_price)는 기준이 다릅니다.**
  두 값을 직접 빼서 등락을 말하지 마세요. 등락은 expected_change_pct를 쓰고,
  기준가를 언급할 때는 baseline_source(무엇을 기준으로 잡았는지)를 같이 밝히세요.
- 도구가 실패하면 **그 사실을 답변에 명시하세요.** 실패를 감추고 아는 척하지 마세요.
- 시세 예측이 Cold Start(유사 아이템 추세 상속)로 나왔다면 그 점을 밝히세요.
  실제 거래 이력이 아니라 추정이라는 뜻이기 때문입니다.
- 검색 결과에는 질의와 무관한 종류가 섞일 수 있습니다. 사용자가 요청한 종류에
  맞는 것만 고르고, 확실하지 않으면 단정하지 마세요.
- 답변은 한국어로, 근거(가격·수치·출처 아이템)를 같이 적으세요."""

_FINAL_PROMPT = (
    "도구 호출 한도에 도달했습니다. 지금까지 얻은 정보만으로 최종 답변을 작성하세요. "
    "확인하지 못한 부분이 있으면 그렇다고 밝히세요."
)


async def run_agent(
    llm_client: LLMClient,
    tenant_code: str,
    query: str,
    max_steps: int | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    max_steps = max_steps or settings.agent_max_steps
    timeout = settings.agent_tool_timeout_seconds

    started = time.perf_counter()
    trace: list[dict[str, Any]] = []

    async with tool_session() as client:
        tools = await openai_tool_schemas(client)
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": _SYSTEM_PROMPT.format(tenant_code=tenant_code),
            },
            {"role": "user", "content": query},
        ]

        stop_reason = "completed"
        answer = ""

        for step in range(1, max_steps + 1):
            result = await llm_client.chat(messages, tools=tools)

            if not result.tool_calls:
                answer = result.content
                break

            messages.append(_assistant_message(result))
            for call in result.tool_calls:
                text, failed = await call_tool_text(
                    client, call.name, call.arguments, timeout
                )
                trace.append(
                    {
                        "step": step,
                        "tool": call.name,
                        "arguments": call.arguments,
                        "failed": failed,
                    }
                )
                messages.append(
                    {"role": "tool", "tool_call_id": call.id, "content": text}
                )
        else:
            # 한도를 다 쓰고도 답이 안 나왔다 — 도구 없이 마무리를 요청한다.
            stop_reason = "max_steps"
            messages.append({"role": "user", "content": _FINAL_PROMPT})
            answer = (await llm_client.chat(messages)).content

    return {
        "answer": answer,
        "tool_calls": trace,
        "tool_failures": sum(1 for entry in trace if entry["failed"]),
        "stop_reason": stop_reason,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
    }


def _assistant_message(result: Any) -> dict[str, Any]:
    """ChatResult → OpenAI 메시지 형식(도구 호출 포함)."""
    return {
        "role": "assistant",
        "content": result.content or None,
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments, ensure_ascii=False),
                },
            }
            for call in result.tool_calls
        ],
    }
