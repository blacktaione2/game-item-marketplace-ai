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
from app.core.progress import STAGE_THINKING, STAGE_TOOL, ProgressFn, noop, safe_emit
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
- **도구 결과의 필드 이름을 답변에 쓰지 마세요.** 사용자는 그 구조를 모릅니다.
  값은 쓰되 이름은 사람이 쓰는 말로 바꿔 말하세요.
- 예측 결과에 estimate_note 가 있으면 그 내용을 반드시 답변에 반영하세요.
  실제 거래 이력이 아니라 추정이라는 뜻이기 때문입니다.
- 검색 결과에는 질의와 무관한 종류가 섞일 수 있습니다. 사용자가 요청한 종류에
  맞는 것만 고르고, 확실하지 않으면 단정하지 마세요.
- **묻지 않은 것을 더 해주겠다고 제안하며 끝내지 마세요.** 물어본 것에 답하고
  끝냅니다. (정보가 모자라 되물어야 할 때는 되물어도 됩니다.)
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
    # **도구 호출마다 알린다.** 복합 질의가 7~25초 걸리는 이유가 여기이고,
    # 사용자가 그동안 알고 싶은 것은 "지금 뭘 하고 있는가"다.
    on_progress: ProgressFn = noop,
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
        # **답변이 어느 아이템을 말하는지 화면이 알아야 한다.**
        # 복합 분기에는 결과 카드가 없어서, 이게 없으면 사용자가 그 아이템으로
        # 갈 방법이 없다 — 시세 분기는 `resolved_item` 으로 이미 해결한 문제인데
        # 여기만 빠져 있었다.
        #
        # `search_items` 결과를 모아두고 `forecast_item_price` 가 고른 id 로
        # 되짚는다. 도구가 이미 실어 보낸 값이라 추가 조회가 없다.
        seen_items: dict[int, dict[str, Any]] = {}
        focus_id: int | None = None
        # 복합 질의의 병목 질문은 "LLM인가 도구인가"다. elapsed_ms 하나로는
        # 답할 수 없어서 둘을 따로 누적한다.
        llm_ms = 0.0
        tool_ms = 0.0

        for step in range(1, max_steps + 1):
            # **호출 전에 알린다.** 이 호출이 6~7초라, 끝난 뒤에만 알리면 그
            # 구간이 통째로 무음이 된다(실측).
            await safe_emit(on_progress, STAGE_THINKING, step=step)
            call_started = time.perf_counter()
            result = await llm_client.chat(messages, tools=tools)
            llm_ms += (time.perf_counter() - call_started) * 1000

            if not result.tool_calls:
                answer = result.content
                break

            messages.append(_assistant_message(result))
            for call in result.tool_calls:
                # **끝났을 때가 아니라 시작할 때 알린다.** 화면은 마지막 줄을
                # "지금 하는 일"로 그리므로 의미가 그래야 맞다. 실측으로 드러난
                # 문제이기도 하다 — 도구 실행이 11초대라, 끝날 때만 알리면 그
                # 시간 내내 "도구를 정하는 중"이라는 **틀린 라벨**이 떠 있었다.
                await safe_emit(on_progress, STAGE_TOOL, tool=call.name, step=step)
                tool_started = time.perf_counter()
                text, failed = await call_tool_text(
                    client, call.name, call.arguments, timeout
                )
                tool_ms += (time.perf_counter() - tool_started) * 1000
                trace.append(
                    {
                        "step": step,
                        "tool": call.name,
                        "arguments": call.arguments,
                        "failed": failed,
                    }
                )
                # 실패만 한 번 더 알린다. 성공은 다음 줄이 뜨는 것으로 이미
                # 드러나지만, **실패는 말하지 않으면 안 보인다.**
                if failed:
                    await safe_emit(
                        on_progress, STAGE_TOOL,
                        tool=call.name, step=step, failed=True,
                    )
                if not failed:
                    _remember_item(call, text, seen_items)
                    if call.name == "forecast_item_price":
                        focus_id = _int_or_none(call.arguments.get("item_id"))
                messages.append(
                    {"role": "tool", "tool_call_id": call.id, "content": text}
                )
        else:
            # 한도를 다 쓰고도 답이 안 나왔다 — 도구 없이 마무리를 요청한다.
            stop_reason = "max_steps"
            messages.append({"role": "user", "content": _FINAL_PROMPT})
            call_started = time.perf_counter()
            answer = (await llm_client.chat(messages)).content
            llm_ms += (time.perf_counter() - call_started) * 1000

    # 예측이 고른 아이템이 있으면 그것, 없으면 검색한 것 중 첫 번째.
    resolved = seen_items.get(focus_id) if focus_id is not None else None
    if resolved is None and seen_items:
        resolved = next(iter(seen_items.values()))

    return {
        "answer": answer,
        "tool_calls": trace,
        "resolved_item": resolved,
        "tool_failures": sum(1 for entry in trace if entry["failed"]),
        "stop_reason": stop_reason,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        "timings": {
            "agent_llm_ms": round(llm_ms, 1),
            "agent_tool_ms": round(tool_ms, 1),
        },
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


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_items(text: str) -> list[dict[str, Any]]:
    """MCP 도구가 돌려준 텍스트에서 아이템 dict 들을 뽑는다.

    **모양을 하나로 가정하면 안 된다.** 도구는 `list[dict]` 를 반환하지만
    MCP 는 그걸 **원소마다 별도 텍스트 블록**으로 직렬화하고,
    `call_tool_text` 가 그것들을 개행으로 이어붙인다. 그래서 실제로 오는 것은

      - 항목이 하나면 → pretty-print 된 **객체 하나**
      - 여럿이면 → 객체들이 줄바꿈으로 **이어 붙은 것**(유효한 JSON 이 아니다)

    둘 다 `json.loads` 로는 리스트가 안 나온다. 그래서 **이어 붙은 값들을
    차례로 읽는다**(`raw_decode`). 배열로 오는 경우도 그대로 받는다 — 직렬화
    방식이 바뀌어도 이 함수는 계속 동작해야 한다.
    """
    decoder = json.JSONDecoder()
    items: list[dict[str, Any]] = []
    index, end = 0, len(text)
    while index < end:
        while index < end and text[index].isspace():
            index += 1
        if index >= end:
            break
        try:
            value, index = decoder.raw_decode(text, index)
        except ValueError:
            # 남은 부분이 JSON 이 아니면 거기서 멈춘다 — 앞에서 읽은 것은 살린다.
            break
        if isinstance(value, list):
            items.extend(entry for entry in value if isinstance(entry, dict))
        elif isinstance(value, dict):
            items.append(value)
    return items


def _remember_item(call: Any, text: str, seen: dict[int, dict[str, Any]]) -> None:
    """`search_items` 결과를 id 로 색인해둔다.

    도구 출력은 문자열로 오므로 다시 파싱한다. **실패해도 조용히 넘긴다** —
    이건 화면 편의를 위한 부가 정보이지 답변의 근거가 아니다. 여기서 예외가
    나면 복합 질의 전체가 죽는데, 그건 얻는 것에 비해 너무 큰 대가다.

    > **그 조용함 때문에 이 함수는 한 번도 동작한 적이 없었다.** 예전 판본은
    > `json.loads(text)` 가 **리스트가 아니면** 그냥 돌아갔는데, MCP 직렬화
    > 때문에 리스트로 오는 경우가 애초에 없었다(`_parse_items` 참고). 그래서
    > 복합 분기의 `resolved_item` 은 늘 `None` 이었고 — 화면에 "이 답변이
    > 가리키는 아이템" 카드가 뜬 적이 없다. 예외도 로그도 안 났다.
    >
    > **부가 정보라서 조용히 넘긴다는 판단은 유지한다.** 바뀐 것은 넘기기 전에
    > 실제로 시도해 보게 된 것뿐이다. 조용한 경로일수록 테스트로 고정해야 한다는
    > 쪽이 이 라운드의 교훈이고, `tests/test_agent_items.py` 가 그 자리다.
    """
    if call.name != "search_items":
        return
    for item in _parse_items(text):
        item_id = _int_or_none(item.get("item_id"))
        if item_id is not None:
            seen[item_id] = item
