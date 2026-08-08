import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from elasticsearch import AsyncElasticsearch
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.core.auth import Actor
from app.core.progress import ProgressFn
from app.core.rate_limit import limit_assistant
from app.services.anomaly.exceptions import AnomalyModelNotTrainedError
from app.services.assistant.pipeline import ask
from app.services.forecast.exceptions import ForecastModelNotTrainedError
from app.services.llm.base import LLMClient
from app.services.llm.dependencies import get_llm_client
from app.services.search.es_client import get_es_client
from app.services.search.exceptions import TenantIndexNotFoundError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/assistant", tags=["assistant"])


class AssistantRequest(BaseModel):
    # tenant_code는 토큰 클레임에서 온다 (ADR-0023). 캐시 키가 테넌트별로
    # 갈리므로, 본문으로 받으면 남의 테넌트 캐시를 조회하게 만들 수도 있었다.
    #
    # **길이 상한이 비용 방어의 일부다** (ADR-0035). 한도 3계층(토큰·20회/분·
    # 50회/일)은 전부 **요청 수**를 센다 — 요청 하나의 길이가 300배가 되면 하루
    # 예산도 300배가 된다. 실측: 19,800자 질의가 200으로 통과하고 16.4초 걸렸다
    # (정상 질의는 ~50자 / 4.5초).
    #
    # 상한을 500자로 둔 근거는 둘이다.
    #   1. 파이프라인이 그 너머를 **보지도 않는다** — 임베딩은 128토큰,
    #      리랭커는 256토큰에서 자른다. 그 뒤 텍스트는 LLM 요금만 늘린다
    #   2. 데이터셋 질의 547건의 **최댓값이 33자**다(중앙 18, p99 31). 15배 여유다
    query: str = Field(min_length=1, max_length=500)
    # 캐시 효과를 측정하거나 디버깅할 때 끌 수 있게 열어둔다.
    use_cache: bool = True


@router.post("")
async def assistant(
    request: AssistantRequest,
    # limit_assistant가 require_actor를 품고 있다 — 인증을 통과한 뒤에 한도를
    # 센다. 순서가 반대면 인증 실패도 한도를 소모한다.
    actor: Actor = Depends(limit_assistant),
    es: AsyncElasticsearch = Depends(get_es_client),
    llm_client: LLMClient = Depends(get_llm_client),
) -> dict[str, Any]:
    try:
        return await ask(
            es=es,
            llm_client=llm_client,
            tenant_code=actor.tenant_code,
            query=request.query,
            use_cache=request.use_cache,
        )
    except TenantIndexNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except (ForecastModelNotTrainedError, AnomalyModelNotTrainedError) as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:
        # **예외 문자열을 클라이언트로 내보내지 않는다** (ADR-0041). 업스트림
        # 메시지에는 ES 인덱스명·쿼리 DSL·내부 호스트가 섞여 나온다. 백엔드는
        # `server.error.include-stacktrace: never` 로 같은 자세를 이미 취하고
        # 있었고, 여기만 안 맞춰져 있었다 — **한쪽만 선언된 설정은 결정이 아니라
        # 누락이다**(`REDIS_PASSWORD` 때와 같은 모양).
        #
        # 대신 서버에는 남긴다. 안 남기면 진단 수단이 같이 사라진다.
        logger.exception("assistant 요청 처리 실패 (tenant=%s)", actor.tenant_code)
        raise HTTPException(status_code=500, detail="요청 처리에 실패했습니다.") from e


# --- 스트리밍 -------------------------------------------------------------
#
# **기존 `POST /api/assistant` 는 그대로 둔다.** 부하 하네스(`load/k6/ai-search.js`)
# 와 `verify-*.sh` 가 거기 걸려 있어서, 갈아치우면 이번 변경에 부하·배포 검증이
# 통째로 딸려온다. 스트리밍은 **덧붙이는 경로**다.
#
# ## 흘리는 것은 토큰이 아니라 단계다
#
# 복합 질의의 7~25초는 대부분 도구 호출이고 그 정보를 `tool_calls` 가 이미 들고
# 있다. 토큰만 흘리면 **마지막 1~2초가 빨라 보일 뿐** 정체 구간은 그대로
# 침묵한다. 자세한 근거는 `app/core/progress.py`.
#
# ## 캐시 적중도 스트림으로 감싼다
#
# "적중이면 스트림을 안 열고 그냥 반환"이 더 빨라 보이지만, 그러면 **프론트가
# 스트리밍 경로와 일반 응답 경로를 둘 다 갖게 된다.** 두 경로는 그 자체로 새
# 버그 표면이고, 적중은 25.9ms(p95)라 아낄 것도 없다. 적중이면 `cache` 이벤트와
# `done` 이 곧바로 나간다 — 모양이 하나다.


def _sse(payload: dict[str, Any]) -> str:
    """SSE 한 건. `data:` 한 줄 + 빈 줄.

    `event:` 이름을 쓰지 않고 payload 안에 `type` 을 둔다 — 프론트가
    `EventSource` 를 못 쓰고(아래) 어차피 직접 파싱하므로, 양쪽 다 코드가 적다.
    """
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.post("/stream")
async def assistant_stream(
    request: AssistantRequest,
    actor: Actor = Depends(limit_assistant),
    es: AsyncElasticsearch = Depends(get_es_client),
    llm_client: LLMClient = Depends(get_llm_client),
) -> StreamingResponse:
    """`POST /api/assistant` 와 같은 일을 하되 진행 상황을 흘린다.

    **인증은 `Authorization` 헤더 그대로다.** 브라우저의 `EventSource` 는 헤더를
    못 붙이므로 프론트는 `fetch` + `ReadableStream` 을 쓴다. 토큰을 쿼리
    파라미터로 옮기는 방법도 있지만 **그러면 nginx 접근 로그에 토큰이 남는다.**
    """

    async def event_stream() -> AsyncIterator[str]:
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

        async def on_progress(stage: str, detail: dict[str, Any]) -> None:
            await queue.put({"type": "progress", "stage": stage, **detail})

        async def runner() -> None:
            try:
                result = await ask(
                    es=es,
                    llm_client=llm_client,
                    tenant_code=actor.tenant_code,
                    query=request.query,
                    use_cache=request.use_cache,
                    on_progress=on_progress,
                )
                await queue.put({"type": "done", "result": result})
            except (TenantIndexNotFoundError, ForecastModelNotTrainedError,
                    AnomalyModelNotTrainedError) as e:
                # **스트림이 열린 뒤에는 상태 코드를 바꿀 수 없다.** 헤더가 이미
                # 나갔으므로 404/503 을 낼 방법이 없고, 오류는 **페이로드**가
                # 된다. 이 셋은 사용자에게 보여줄 수 있는 메시지들이다.
                await queue.put({"type": "error", "message": str(e)})
            except Exception:
                # 위와 같은 이유로 500 을 못 낸다. **예외 문자열은 내보내지
                # 않는다** (ADR-0041) — 비스트리밍 경로와 같은 자세다.
                logger.exception(
                    "assistant 스트림 처리 실패 (tenant=%s)", actor.tenant_code
                )
                await queue.put({"type": "error", "message": "요청 처리에 실패했습니다."})
            finally:
                await queue.put(None)

        task = asyncio.create_task(runner())
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield _sse(item)
        finally:
            # 클라이언트가 끊으면 제너레이터가 닫히고 여기로 온다. **하던 일을
            # 취소한다** — 안 그러면 아무도 안 읽는 답변에 LLM 요금이 계속 나간다.
            # 이미 끝난 태스크에 걸어도 무해하다.
            task.cancel()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            # **이게 없으면 배포에서만 아무것도 안 흐른다.** nginx 는 프록시
            # 응답을 기본으로 버퍼링해서, 전부 끝난 뒤 한꺼번에 내보낸다 —
            # 로컬 dev 서버에는 프록시 버퍼가 없어 개발 중에는 멀쩡해 보인다.
            #
            # `nginx.conf` 에 `proxy_buffering off` 를 넣는 방법도 있지만 이
            # 헤더를 고른 이유가 둘이다: **응답 단위**라 다른 경로의 버퍼링을
            # 건드리지 않고, 설정이 아니라 앱에 붙어 있어 배포 구성이 바뀌어도
            # 같이 따라간다.
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache",
        },
    )
