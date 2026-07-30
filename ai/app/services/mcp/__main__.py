"""stdio 진입점 — 외부 MCP 클라이언트가 이 서버에 붙을 수 있게 한다.

실행: python -m app.services.mcp   (ai/ 에서)

에이전트는 같은 서버 정의를 인메모리 트랜스포트로 쓰지만(session.py),
이 진입점이 있어야 서버가 프로세스 안에서만 쓰이는 함수 묶음이 아니라
**실제 MCP 서버**임이 드러난다. Claude Desktop 등 외부 클라이언트 설정에
그대로 넣을 수 있다.

주의: 이 프로세스는 모델(임베딩/예측/이상탐지)을 따로 로드하므로 FastAPI
서버와 같이 띄우면 메모리를 두 배로 쓴다. 공유 인프라(4 OCPU/24GB)에서는
데모 목적으로만 띄울 것.
"""

import asyncio

from app.services.mcp.server import build_server


def main() -> None:
    asyncio.run(build_server().run_stdio_async())


if __name__ == "__main__":
    main()
