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
from app.core.ids import MalformedIdRefError, UnsupportedIdSpaceError
from app.core.progress import ProgressFn, noop
from app.core.rate_limit import consume_daily, limit_assistant
from app.services.anomaly.exceptions import (
    AnomalyModelNotTrainedError,
    TradeNotFoundError,
    UnknownTenantError,
)
from app.services.assistant.pipeline import ask
from app.services.forecast.exceptions import (
    ForecastModelNotTrainedError,
    HorizonTooLongError,
    InsufficientHistoryError,
    ItemNotFoundError,
)
from app.services.llm.base import LLMClient
from app.services.llm.dependencies import get_llm_client
from app.services.search.es_client import get_es_client
from app.services.search.exceptions import TenantIndexNotFoundError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/assistant", tags=["assistant"])

#: 사용자에게 그대로 보여줄 수 있는 예외 → 비스트리밍 경로의 상태 코드.
#:
#: **두 라우트가 이 표 하나에서 나온다.** 비스트리밍은 상태 코드로, 스트리밍은
#: 페이로드로 옮길 뿐이다. 한쪽만 늘리면 같은 예외가 한쪽에서는 안내가 되고 다른
#: 쪽에서는 500 이 된다 (ADR-0049).
#:
#: > **ADR-0049 는 그 문장을 적어놓고 목록을 둘로 뒀다** (ADR-0050). 튜플은
#: > 스트리밍만 쓰고, 비스트리밍은 `except` 절 셋에 같은 여섯 개를 **다시**
#: > 열거했다. 오늘 두 목록이 일치하는 것은 같은 사람이 같은 날 적었기
#: > 때문이지 구조 때문이 아니다 — 이 저장소가 반복해서 겪은
#: > *"열거는 새고, 같은 열거로 만든 검사는 그걸 못 잡는다"* 의 재발이다.
#: > 이제 목록이 하나고, `_showable_status()` 가 상태 코드를 여기서 읽는다.
#:
#: > **그리고 그 목록 자체가 짧았다** (ADR-0050 정정). 첫 판본은 ADR-0049 가 적은
#: > 여섯 개를 그대로 옮겼는데, **개별 라우터가 매핑하는 것은 아홉 개**였다 —
#: > `TradeNotFoundError` 가 빠져서 `"거래 999999번 이상거래인지 확인해줘"` 가
#: > 배포에서 **500** 이었다(`/api/anomaly/detect` 는 같은 조건에 404). 즉
#: > *열거가 한 층 위로 샜다*: 목록을 하나로 합치면서 **무엇이 그 목록에 들어가야
#: > 하는지**를 다시 열거로 정한 것이다.
#: >
#: > 이제 `test_assistant_stream.py` 가 세 라우터(`anomaly`·`forecast`·`search`)의
#: > `except` 절을 AST 로 읽어 **여기에 같은 상태 코드로 다 있는지** 본다 — 목록을
#: > 적는 대신 **물어본다.**
#:
#: 개별 라우터가 매핑하지만 통합 진입점으로는 못 닿는 것도 넣는다
#: (`MalformedIdRefError`·`UnsupportedIdSpaceError` — 어시스턴트는 `parse_ref` 를
#: 안 거치고 `IdSpace.SYNTHETIC` 을 고정으로 넘긴다). **닿는지 여부로 거르지
#: 않는 이유**: 그 판단이 곧 다음에 새는 열거이고, 못 닿는 항목의 대가는 쓰이지
#: 않는 표 한 줄뿐이다.
_SHOWABLE_STATUS: dict[type[Exception], int] = {
    TenantIndexNotFoundError: 404,
    ItemNotFoundError: 404,
    UnknownTenantError: 404,
    TradeNotFoundError: 404,
    HorizonTooLongError: 400,
    InsufficientHistoryError: 422,
    ForecastModelNotTrainedError: 503,
    AnomalyModelNotTrainedError: 503,
    MalformedIdRefError: 400,
    UnsupportedIdSpaceError: 501,
}

#: `except` 절이 쓰는 형태. 표에서 유도하므로 따로 늘어날 수 없다.
_SHOWABLE = tuple(_SHOWABLE_STATUS)


def _showable_status(exc: Exception) -> int:
    """`_SHOWABLE` 로 걸러진 예외의 상태 코드.

    `except` 는 isinstance 기준이므로 하위 클래스가 잡힐 수 있다. `type(exc)` 로
    바로 찾으면 그때 KeyError 가 나므로 같은 기준으로 찾는다.
    """
    return next(
        status for cls, status in _SHOWABLE_STATUS.items() if isinstance(exc, cls)
    )


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


async def _ask_metered(
    actor: Actor,
    es: AsyncElasticsearch,
    llm_client: LLMClient,
    request: AssistantRequest,
    on_progress: ProgressFn = noop,
) -> dict[str, Any]:
    """`ask()` + 일일 예산 정산. **두 라우트가 이 함수만 부른다.**

    한 곳에 둔 이유는 규칙이 갈라지지 않게 하기 위해서다 — 스트리밍 경로가
    일일 한도를 안 소비하면 그게 곧 상한 우회로다.

    **일일 예산은 캐시 적중이면 소비하지 않는다** (ADR-0044). 분당 한도는
    의존성이 이미 요청 앞에서 올렸다 — 목적이 달라서다(`consume_daily` 참조).

    ## 나가는 길이 셋인데 예외 두 개만 막고 있었다

    이 함수는 예전에 `except Exception` 으로 실패 경로를 잡았다. **취소는 그
    그물을 통과한다** — `asyncio.CancelledError` 는 `Exception` 이 아니라
    `BaseException` 을 상속하기 때문이다. 그리고 스트리밍 경로는 클라이언트가
    끊길 때마다 `task.cancel()` 을 부른다(아래 `assistant_stream`).

    결과: **탭을 닫거나 연결이 끊기면 LLM 호출은 이미 나갔는데 하루 예산은 안
    깎였다.** ADR-0044 가 막은 것(비용 0인 캐시 적중이 예산을 깎던 것)의
    거울상이고, 같은 계층에 난 반대 방향 구멍이다.

    그래서 **나가는 길을 세는 대신 `finally` 로 옮겼다.** "적중이 아니면
    소비한다" 한 문장만 남고, 앞으로 어떤 예외 계열이 늘어도 규칙이 새지 않는다.
    `await` 를 `finally` 에 두는 것이 취소 중에도 도는 이유: 취소는 한 번
    전달되고, 그 뒤의 `await` 는 정상적으로 스케줄된다. `consume_daily` 자체가
    Redis 실패를 삼키므로 여기서 두 번 터질 일도 없다.
    """
    hit = False
    try:
        result = await ask(
            es=es,
            llm_client=llm_client,
            tenant_code=actor.tenant_code,
            query=request.query,
            use_cache=request.use_cache,
            on_progress=on_progress,
        )
        hit = bool(result.get("cache", {}).get("hit", False))
        return result
    finally:
        # **실패도 취소도 소비한다.** 여기까지 왔으면 LLM 호출이 이미 나갔을 수
        # 있고, 공짜로 끝나는 경로를 하나라도 남기면 그게 곧 우회로다.
        if not hit:
            await consume_daily(actor)


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
        return await _ask_metered(actor, es, llm_client, request)
    # **개별 라우터가 매핑하는 예외를 여기서도 매핑한다** (ADR-0049). 예전에는 셋만
    # 잡아서, 같은 파이프라인이 같은 예외를 던져도 `/api/forecast` 로는 404·422 가
    # 나가고 `/api/assistant` 로는 **500** 이 나갔다. 콜드스타트 donor 가 없는
    # 아이템을 통합 진입점으로 물으면 "요청 처리에 실패했습니다" 가 돌아온다.
    # 이웃 라우터가 이미 판정해둔 것을 물려받지 못한 자리다(ADR-0047 의 주제).
    #
    # **절을 예외마다 나누지 않는다** (ADR-0050). 나누면 그게 곧 두 번째 열거이고,
    # 스트리밍 경로의 `_SHOWABLE` 과 어긋나도 아무 신호가 없다.
    except _SHOWABLE as e:
        raise HTTPException(status_code=_showable_status(e), detail=str(e)) from e
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
                # 비스트리밍 경로와 **같은 함수**를 부른다. 일일 예산 규칙이
                # 갈라지면 이쪽이 곧 상한 우회로가 된다.
                result = await _ask_metered(
                    actor, es, llm_client, request, on_progress
                )
                await queue.put({"type": "done", "result": result})
            except _SHOWABLE as e:
                # **스트림이 열린 뒤에는 상태 코드를 바꿀 수 없다.** 헤더가 이미
                # 나갔으므로 404/503 을 낼 방법이 없고, 오류는 **페이로드**가
                # 된다. 이것들은 사용자에게 보여줄 수 있는 메시지들이다.
                #
                # **목록을 비스트리밍 경로와 공유한다** (ADR-0049). 예전에는 여기만
                # 셋이라, 같은 예외가 한쪽에서는 안내 문장이 되고 다른 쪽에서는
                # "요청 처리에 실패했습니다" 가 됐다.
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
