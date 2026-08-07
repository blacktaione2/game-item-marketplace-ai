"""OpenAI 장애 시 Claude 로 넘어간다 — ADR-0042.

**네트워크 없이 돈다.** 실제 프로바이더 왕복은 한 번 손으로 확인했고(ADR 에 기록),
여기서 고정하는 것은 그때 확인한 성질이 코드에 남아 있는가다.

가장 중요한 것은 **형식 번역**이다. `LLMClient` 인터페이스는 중립인데 그 위의
에이전트 루프가 OpenAI 메시지 형식을 그대로 쓴다. 인터페이스만 맞추고 형식을 안
맞추면 `complete()` 는 되고 **에이전트만 조용히 깨진다** — 폴백이 가장 필요한
순간에.
"""

import asyncio

import pytest

from app.services.llm.anthropic_client import (
    _split_system,
    _to_anthropic_tool,
    _to_chat_result,
)
from app.services.llm.base import ChatResult, LLMClient
from app.services.llm.fallback import FallbackLLMClient


class _Dead(LLMClient):
    def __init__(self):
        self.calls = 0

    async def chat(self, messages, tools=None):
        self.calls += 1
        raise RuntimeError("프로바이더 장애")


class _Alive(LLMClient):
    def __init__(self, content="폴백 응답"):
        self.calls = 0
        self._content = content

    async def chat(self, messages, tools=None):
        self.calls += 1
        return ChatResult(content=self._content)


class TestFailover:
    def test_primary_failure_is_answered_by_the_secondary(self):
        primary, secondary = _Dead(), _Alive()
        client = FallbackLLMClient(primary, secondary)
        assert asyncio.run(client.complete("질문")) == "폴백 응답"
        assert primary.calls == 1 and secondary.calls == 1

    def test_healthy_primary_never_touches_the_secondary(self):
        primary, secondary = _Alive("1차 응답"), _Alive("폴백 응답")
        client = FallbackLLMClient(primary, secondary)
        assert asyncio.run(client.complete("질문")) == "1차 응답"
        assert secondary.calls == 0

    def test_both_down_raises_the_primary_error(self):
        """**2차 예외로 바꿔치면 진단이 엉뚱한 곳을 가리킨다.**

        1차가 죽어서 시작된 일인데 로그에는 폴백 오류만 남는다.
        """
        class _OtherDead(LLMClient):
            async def chat(self, messages, tools=None):
                raise ValueError("폴백도 장애")

        client = FallbackLLMClient(_Dead(), _OtherDead())
        with pytest.raises(RuntimeError, match="프로바이더 장애"):
            asyncio.run(client.complete("질문"))

    def test_a_success_clears_the_failure_streak(self):
        """연속 실패여야 한다. 누적이면 오래 돌수록 반드시 열린다."""
        class _Flaky(LLMClient):
            def __init__(self):
                self.n = 0

            async def chat(self, messages, tools=None):
                self.n += 1
                if self.n % 2:
                    raise RuntimeError("가끔 실패")
                return ChatResult(content="ok")

        client = FallbackLLMClient(_Flaky(), _Alive(), failure_threshold=3)
        for _ in range(6):
            asyncio.run(client.complete("질문"))
        assert client.circuit_open is False


class TestCircuitBreaker:
    """예외만 잡아 넘기면 장애 동안 **모든 요청이 1차 타임아웃을 먼저 문다.**

    fail-open 에는 정합성 축 말고 지연 축이 있다 — RabbitMQ 라운드에서 기본 연결
    타임아웃 60초 때문에 "구매는 성공하는데 1분 걸린다" 가 됐던 그것이다.
    """

    def test_opens_at_the_threshold_and_stops_calling_the_primary(self):
        primary, secondary = _Dead(), _Alive()
        client = FallbackLLMClient(primary, secondary, failure_threshold=3)
        for _ in range(5):
            asyncio.run(client.complete("질문"))
        # 3회에서 열리므로 그 뒤 2회는 1차를 아예 안 부른다.
        assert primary.calls == 3
        assert secondary.calls == 5
        assert client.circuit_open is True

    def test_closes_again_after_the_reset_window(self):
        primary, secondary = _Dead(), _Alive()
        client = FallbackLLMClient(primary, secondary, failure_threshold=1,
                                   reset_seconds=0.0)
        asyncio.run(client.complete("질문"))
        # reset_seconds=0 이면 즉시 다시 닫힌다 — 시간을 기다리지 않고 본다.
        assert client.circuit_open is False

    def test_one_more_failure_after_reset_reopens_immediately(self):
        """리셋 뒤 탐침 하나가 또 실패하면 임계값을 다시 채우지 않고 바로 연다.

        상태를 하나 더(반열림) 두지 않는 대신 카운터를 임계값 직전으로 되돌려
        같은 효과를 낸다.
        """
        primary, secondary = _Dead(), _Alive()
        client = FallbackLLMClient(primary, secondary, failure_threshold=3,
                                   reset_seconds=0.0)
        for _ in range(3):
            asyncio.run(client.complete("질문"))
        assert client.circuit_open is False  # 리셋창이 0이라 이미 닫혔다
        asyncio.run(client.complete("질문"))  # 탐침 1회
        assert client._consecutive_failures >= 3


class TestMessageTranslation:
    """OpenAI 형식 → Anthropic 형식. **여기가 틀리면 에이전트만 조용히 깨진다.**"""

    def test_system_becomes_a_top_level_parameter(self):
        system, messages = _split_system(
            [{"role": "system", "content": "너는 도우미다"},
             {"role": "user", "content": "안녕"}]
        )
        assert system == "너는 도우미다"
        assert messages == [{"role": "user", "content": "안녕"}]

    def test_tool_results_become_user_blocks(self):
        _, messages = _split_system(
            [{"role": "tool", "tool_call_id": "call_1", "content": "결과"}]
        )
        assert messages == [
            {"role": "user",
             "content": [{"type": "tool_result", "tool_use_id": "call_1",
                          "content": "결과"}]}
        ]

    def test_consecutive_tool_results_are_merged_into_one_message(self):
        """**도구를 하나만 부르는 동안에는 안 보이다가 둘을 부르면 터진다.**

        Anthropic 은 역할이 번갈아 나오길 기대하므로, 연속된 `tool` 메시지를
        그대로 두면 400 이다.
        """
        _, messages = _split_system(
            [{"role": "tool", "tool_call_id": "a", "content": "1"},
             {"role": "tool", "tool_call_id": "b", "content": "2"}]
        )
        assert len(messages) == 1
        assert [block["tool_use_id"] for block in messages[0]["content"]] == ["a", "b"]

    def test_assistant_tool_calls_become_tool_use_blocks(self):
        _, messages = _split_system(
            [{"role": "assistant", "content": None,
              "tool_calls": [{"id": "c1", "type": "function",
                              "function": {"name": "search_items",
                                           "arguments": '{"query": "검"}'}}]}]
        )
        block = messages[0]["content"][0]
        assert block == {"type": "tool_use", "id": "c1", "name": "search_items",
                         "input": {"query": "검"}}

    def test_an_assistant_turn_with_nothing_in_it_is_dropped(self):
        """빈 블록 목록을 보내면 400 이다."""
        _, messages = _split_system(
            [{"role": "assistant", "content": None, "tool_calls": []}]
        )
        assert messages == []

    def test_broken_tool_arguments_do_not_raise(self):
        """`openai_client` 와 같은 자세다 — 여기서 터뜨리면 에이전트가 스스로
        고쳐 부를 기회가 사라진다."""
        _, messages = _split_system(
            [{"role": "assistant", "content": None,
              "tool_calls": [{"id": "c1",
                              "function": {"name": "f", "arguments": "{망가짐"}}]}]
        )
        assert messages[0]["content"][0]["input"] == {}


class TestToolSchemaTranslation:
    def test_parameters_becomes_input_schema(self):
        converted = _to_anthropic_tool(
            {"type": "function",
             "function": {"name": "search_items", "description": "검색",
                          "parameters": {"type": "object",
                                         "properties": {"query": {"type": "string"}}}}}
        )
        assert converted["name"] == "search_items"
        assert converted["input_schema"]["properties"] == {"query": {"type": "string"}}
        assert "parameters" not in converted

    def test_a_tool_without_parameters_still_gets_a_valid_schema(self):
        converted = _to_anthropic_tool({"function": {"name": "ping"}})
        assert converted["input_schema"] == {"type": "object", "properties": {}}


class TestResponseParsing:
    def test_text_blocks_are_joined(self):
        class _Block:
            def __init__(self, **kw):
                self.__dict__.update(kw)

        response = _Block(content=[_Block(type="text", text="가"),
                                   _Block(type="text", text="나")])
        assert _to_chat_result(response).content == "가나"

    def test_tool_use_blocks_become_tool_calls(self):
        class _Block:
            def __init__(self, **kw):
                self.__dict__.update(kw)

        response = _Block(content=[
            _Block(type="tool_use", id="t1", name="forecast_item_price",
                   input={"item_id": 3})
        ])
        result = _to_chat_result(response)
        assert result.content == ""
        assert result.tool_calls[0].name == "forecast_item_price"
        assert result.tool_calls[0].arguments == {"item_id": 3}


class TestWiring:
    """키가 없으면 **폴백을 만들지 않는다.**

    없는 폴백을 있는 척 감싸면 장애가 나서야 "폴백이 없었다" 를 알게 된다.
    """

    def test_no_key_means_no_wrapper(self, monkeypatch):
        from app.core.config import get_settings
        from app.services.llm import dependencies

        get_settings.cache_clear()
        dependencies.get_llm_client.cache_clear()
        monkeypatch.setenv("ANTHROPIC_API_KEY", "")
        client = dependencies.get_llm_client()
        assert not isinstance(client, FallbackLLMClient)
        dependencies.get_llm_client.cache_clear()
        get_settings.cache_clear()

    def test_a_key_produces_the_fallback_wrapper(self, monkeypatch):
        from app.core.config import get_settings
        from app.services.llm import dependencies

        get_settings.cache_clear()
        dependencies.get_llm_client.cache_clear()
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        client = dependencies.get_llm_client()
        assert isinstance(client, FallbackLLMClient)
        dependencies.get_llm_client.cache_clear()
        get_settings.cache_clear()

    def test_the_fallback_runs_at_the_same_temperature(self):
        """폴백이 더 흔들리면 장애 때 답이 **더 나빠진다** — 목적과 정반대다."""
        import inspect

        source = inspect.getsource(
            __import__("app.services.llm.dependencies", fromlist=["x"])
        )
        assert "temperature=settings.openai_temperature" in source
