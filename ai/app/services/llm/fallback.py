"""1차 프로바이더가 죽으면 2차로 넘긴다 — 서킷 브레이커 포함 (ADR-0042).

ADR-0004 가 적어둔 것: *"OpenAI 장애가 정적 메시지가 아니라 실제로 다른 LLM 으로
페일오버하도록"*. 이 파일이 그 자리다.

## 왜 그냥 try/except 가 아닌가 — 지연 축이 있다

예외만 잡아 넘기면 장애 동안 **모든 요청이 OpenAI 타임아웃을 먼저 물고** 폴백으로
간다. 이 저장소는 그 대가를 이미 한 번 치렀다: RabbitMQ 를 도입할 때 publish 를
fail-open 으로 감쌌는데 AMQP 기본 연결 타임아웃이 60초라 **"구매는 성공하는데 1분
걸린다"** 가 됐다. fail-open 에는 정합성 축 말고 **지연 축**이 있다.

그래서 연속 실패가 쌓이면 **1차를 잠깐 건너뛴다.** 열려 있는 동안은 폴백만 쓴다.

## 새 의존성을 들이지 않는다

`pybreaker` 같은 것을 쓸 수 있지만, 필요한 것은 상태 셋과 카운터 하나다. 백엔드가
Redisson `RRateLimiter` 를, AI 서버가 `redis.asyncio` 를 **이미 가진 것으로**
한도를 만든 것과 같은 판단이다(ADR-0024).

**프로세스 내 상태다.** 인스턴스가 여럿이면 각자 배운다 — Redis 로 공유할 수도
있지만, 이건 비용·정합성이 아니라 *"이 프로세스가 방금 겪은 일"* 이라 공유할 이유가
약하고 Redis 장애가 LLM 경로를 물게 만든다.

## 폴백도 실패하면 원래 예외를 올린다

2차의 예외로 바꿔치면 진단이 엉뚱한 곳을 가리킨다 — 1차가 죽어서 시작된 일인데
로그에는 Anthropic 오류만 남는다. 둘 다 죽으면 **1차 예외**를 올리고, 그 위에서
ADR-0041 의 내려앉기가 받는다.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from app.services.llm.base import ChatResult, LLMClient

logger = logging.getLogger(__name__)


class FallbackLLMClient(LLMClient):
    """`primary` 를 쓰다 실패하면 `secondary` 로 넘긴다."""

    def __init__(
        self,
        primary: LLMClient,
        secondary: LLMClient,
        failure_threshold: int = 3,
        reset_seconds: float = 60.0,
    ) -> None:
        self._primary = primary
        self._secondary = secondary
        self._failure_threshold = failure_threshold
        self._reset_seconds = reset_seconds
        self._consecutive_failures = 0
        self._opened_at: float | None = None

    @property
    def circuit_open(self) -> bool:
        """1차를 건너뛰는 중인가. 리셋 시간이 지나면 저절로 닫힌다."""
        if self._opened_at is None:
            return False
        if time.monotonic() - self._opened_at >= self._reset_seconds:
            # **반열림 없이 그냥 닫는다.** 다음 요청 하나가 탐침 역할을 하고,
            # 그게 또 실패하면 즉시 다시 열린다(임계값이 아니라 1회로 — 아래).
            # 상태를 하나 더 두는 값어치가 없다.
            logger.info("LLM 서킷 닫힘 — 1차를 다시 시도한다")
            self._opened_at = None
            self._consecutive_failures = self._failure_threshold - 1
            return False
        return True

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> ChatResult:
        if self.circuit_open:
            # 1차를 아예 안 부른다. 이게 이 클래스가 try/except 보다 나은 이유다.
            return await self._secondary.chat(messages, tools=tools)

        try:
            result = await self._primary.chat(messages, tools=tools)
        except Exception as primary_error:
            self._record_failure()
            logger.warning(
                "1차 LLM 실패 — 폴백으로 넘긴다 (연속 %d회)",
                self._consecutive_failures,
                exc_info=True,
            )
            try:
                return await self._secondary.chat(messages, tools=tools)
            except Exception:
                # **원래 예외를 올린다.** 2차 예외로 바꿔치면 진단이 엉뚱한 곳을
                # 가리킨다 — 1차가 죽어서 시작된 일이다.
                logger.warning("폴백 LLM 도 실패했다", exc_info=True)
                raise primary_error

        self._consecutive_failures = 0
        return result

    def _record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._failure_threshold and self._opened_at is None:
            logger.warning(
                "LLM 서킷 열림 — %.0f초 동안 1차를 건너뛴다", self._reset_seconds
            )
            self._opened_at = time.monotonic()
