"""MCP 클라이언트 세션과 OpenAI 도구 스키마 변환.

트랜스포트는 **인메모리**다. `Client(server)`에 서버 객체를 그대로 넘기면
SDK가 인메모리 스트림 페어로 연결한다 — JSON-RPC 핸드셰이크, `tools/list`,
`tools/call`이 모두 실제 프로토콜대로 오간다. 다른 점은 바이트가 소켓이
아니라 메모리 스트림을 지난다는 것뿐이다.

HTTP 마운트 대신 이걸 고른 이유는 에이전트가 같은 FastAPI 앱 안에 있어서
자기 자신에게 HTTP를 호출해야 하고, 알려진 이벤트 루프 블로킹(로드맵 기술
부채)과 겹치면 교착 위험이 있기 때문이다. 별도 프로세스는 모델을 두 벌
로드해서 공유 인프라에 부담이다. (ADR-0011)

세션은 요청마다 연다. 인메모리 핸드셰이크는 저렴하고, 장수명 세션을 두면
앱 수명주기 관리가 붙는데 그만한 이득이 없다.
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import Any, AsyncIterator

from mcp import Client
from mcp.server.mcpserver import MCPServer

from app.services.mcp.server import build_server

logger = logging.getLogger(__name__)


@lru_cache
def get_server() -> MCPServer:
    """도구 등록은 한 번만 — 서버 객체는 프로세스 수명 동안 공유한다."""
    return build_server()


@asynccontextmanager
async def tool_session() -> AsyncIterator[Client]:
    async with Client(get_server()) as client:
        yield client


async def openai_tool_schemas(client: Client) -> list[dict[str, Any]]:
    """MCP `tools/list` 결과 → OpenAI chat completions의 tools 파라미터 형식.

    MCP가 타입 힌트에서 뽑아준 JSON Schema를 그대로 쓴다. 도구 정의를 두 벌
    관리하지 않는 게 이 변환의 요점이다.
    """
    listed = await client.list_tools()
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description or "",
                "parameters": tool.input_schema,
            },
        }
        for tool in listed.tools
    ]


async def call_tool_text(
    client: Client,
    name: str,
    arguments: dict[str, Any],
    timeout_seconds: float,
) -> tuple[str, bool]:
    """도구를 호출하고 (모델에게 돌려줄 텍스트, 실패 여부)를 반환.

    **예외를 밖으로 던지지 않는다.** 도구 실패는 에이전트에게 결과로 돌아가야
    스스로 다른 도구를 쓰거나 부분 결과로 답할 수 있다. 예외로 터뜨리면
    요청 전체가 죽는다.

    MCP는 도구 안에서 난 예외를 이미 `is_error=True`로 감싸주므로, 여기서
    추가로 막을 것은 타임아웃과 트랜스포트 오류다.
    """
    try:
        result = await client.call_tool(
            name, arguments, read_timeout_seconds=timeout_seconds
        )
    except Exception as e:
        logger.warning("도구 호출 실패: %s (%s)", name, e, exc_info=True)
        return json.dumps(
            {"error": f"도구 '{name}' 호출이 실패했습니다: {e}"}, ensure_ascii=False
        ), True

    text = "\n".join(
        block.text for block in result.content if hasattr(block, "text")
    )
    return text or "(빈 응답)", bool(result.is_error)
