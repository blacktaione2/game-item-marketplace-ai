"""temperature가 실제로 API 호출에 실리는지.

**빠뜨리기 쉬운 종류의 결함이라 테스트로 고정한다.** 이 프로젝트는 오랫동안
`temperature`를 안 넘겼고, OpenAI 기본값 1.0으로 돌면서 재작성이 실행마다
달라졌다. 코드는 정상으로 보였고 에러도 없었다 — 없는 파라미터는 눈에 띄지
않는다(ADR-0017).

여기서 재는 건 "값이 kwargs에 들어가는가" 하나다. 모델의 실제 결정성은
`scripts/evaluate_rewrite_determinism.py`가 실측으로 판단한다.

`pytest-asyncio`를 새로 넣지 않고 `asyncio.run`으로 돌린다 — 기존 테스트가
전부 동기 단위 테스트라 이것 하나 때문에 의존성을 늘릴 이유가 없다.
"""

import asyncio

from app.core.config import Settings
from app.services.llm.openai_client import OpenAIClient


class FakeMessage:
    content = "ok"
    tool_calls = None


class FakeChoice:
    message = FakeMessage()


class FakeResponse:
    choices = [FakeChoice()]


class RecordingCompletions:
    """`chat.completions.create` 호출 인자를 받아 적는다."""

    def __init__(self) -> None:
        self.kwargs: dict = {}

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return FakeResponse()


def build(temperature: float | None = None) -> tuple[OpenAIClient, RecordingCompletions]:
    client = (
        OpenAIClient(api_key="test", model="gpt-4o-mini")
        if temperature is None
        else OpenAIClient(api_key="test", model="gpt-4o-mini", temperature=temperature)
    )
    recorder = RecordingCompletions()
    client._client.chat.completions = recorder  # type: ignore[assignment]
    return client, recorder


class TestTemperatureReachesTheAPI:
    def test_it_is_always_sent_explicitly(self):
        """생략하면 API가 1.0을 쓴다 — 명시적으로 실려야 한다."""
        client, recorder = build(0.0)
        asyncio.run(client.chat([{"role": "user", "content": "hi"}]))
        assert "temperature" in recorder.kwargs
        assert recorder.kwargs["temperature"] == 0.0

    def test_the_default_is_zero(self):
        """인자를 안 주면 0이다. 서비스 경로의 기본값은 결정적이어야 한다."""
        client, recorder = build()
        asyncio.run(client.chat([{"role": "user", "content": "hi"}]))
        assert recorder.kwargs["temperature"] == 0.0

    def test_a_higher_value_is_honoured(self):
        """하드네거티브 생성은 다양성이 자산이라 높은 온도를 쓴다."""
        client, recorder = build(1.0)
        asyncio.run(client.chat([{"role": "user", "content": "hi"}]))
        assert recorder.kwargs["temperature"] == 1.0

    def test_complete_goes_through_the_same_path(self):
        """`complete()`는 `chat()` 위의 래퍼라 같은 설정을 받아야 한다."""
        client, recorder = build(0.0)
        asyncio.run(client.complete("hi"))
        assert recorder.kwargs["temperature"] == 0.0


class TestSettingsDefault:
    def test_service_default_is_deterministic(self):
        assert Settings(_env_file=None).openai_temperature == 0.0
