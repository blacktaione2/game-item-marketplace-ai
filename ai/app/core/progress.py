"""파이프라인 진행 상황 콜백.

**무엇을 흘리는가 — 토큰이 아니라 단계다.**

복합 질의의 7~25초는 대부분 **도구 호출**이고, 그 정보를 `tool_calls` 가 이미
들고 있다. 토큰만 흘리면 마지막 1~2초가 빨라 보일 뿐 정체 구간은 그대로
침묵한다. 사용자가 기다리는 동안 알고 싶은 것은 "글자가 나오기 시작했는가"가
아니라 **"지금 뭘 하고 있는가"** 다.

부수적으로, 토큰 스트리밍은 `LLMClient.chat()` 을 통째로 스트리밍 API 로
바꿔야 하고 그러면 OpenAI/Anthropic 양쪽 번역을 다시 짜야 한다(ADR-0042).
단계 이벤트는 이미 있는 계측점에 콜백 한 줄을 붙이는 것으로 끝난다.

**콜백은 실패해도 파이프라인을 죽이지 않는다.** 진행 표시는 부가 정보이지
답변의 일부가 아니다 — 클라이언트가 끊어졌다고 요청이 실패해서는 안 된다.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

# (stage, detail) -> None. stage 는 아래 상수 중 하나다.
ProgressFn = Callable[[str, dict[str, Any]], Awaitable[None]]

# 단계 이름. 화면 문구는 프론트가 정하고, 서버는 **무슨 일이 일어났는지만** 준다.
STAGE_CACHE = "cache"        # 캐시 조회 끝남. detail: hit
STAGE_ROUTING = "routing"    # 의도 판정 끝남. detail: intent, decided_by
STAGE_BRANCH = "branch"      # 분기 실행 시작. detail: intent
STAGE_THINKING = "thinking"  # 에이전트가 다음 도구를 고르는 중. detail: step
STAGE_TOOL = "tool"          # 에이전트 도구 호출 끝남. detail: tool, step, failed

# **`thinking` 은 측정해보고 추가했다.** 처음엔 도구 호출이 끝날 때만 알렸는데,
# 실측하니 `branch` 와 첫 `tool` 사이가 **6.6초 무음**이었다 — 그게 LLM 이
# 어떤 도구를 부를지 정하는 시간이고, 진행 표시를 넣은 이유가 바로 그 구간이다.
# 끝난 것만 알리면 **가장 긴 대기가 그대로 침묵**으로 남는다.


async def noop(stage: str, detail: dict[str, Any]) -> None:
    """스트리밍이 아닐 때의 기본값. 기존 호출자는 아무것도 바꾸지 않아도 된다."""
    return None


async def safe_emit(on_progress: ProgressFn, stage: str, **detail: Any) -> None:
    """콜백을 부르되 예외를 삼킨다.

    **조용히 삼키지는 않는다** — 이 저장소는 `except: pass` 하나 때문에 캐시가
    죽은 걸 아무도 몰랐던 전례가 있다(ADR-0042). 열어두되 기록한다.
    """
    try:
        await on_progress(stage, detail)
    except Exception:
        logger.warning("진행 이벤트 전송 실패 — 파이프라인은 계속한다", exc_info=True)
