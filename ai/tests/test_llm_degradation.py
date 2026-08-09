"""설명 LLM 이 죽었을 때 500 대신 결정적 문장으로 내려앉는다 — ADR-0041.

**LLM 없이 전부 돈다.** 터지는 클라이언트를 넣고 무엇이 나오는지 본다.

이 라운드가 고치는 것은 관측된 결함이 아니라 **관측 가능한 실패 모드**다:
프로바이더가 죽으면 검색과 FAQ 는 살아남는데(각각 폴백과 LLM 0회) 시세·이상거래는
`/api/assistant` 의 포괄 예외에 걸려 **500** 이었다. 그 시점에 답에 필요한 값은
이미 다 계산돼 있었다 — 없는 것은 산문뿐이다.
"""

import asyncio

import pytest

from app.core.metrics import _outcome
from app.services.assistant.pipeline import (
    _anomaly_answer,
    _forecast_answer,
    _timed_complete,
)


class _Broken:
    async def complete(self, prompt: str) -> str:
        raise RuntimeError("업스트림 장애")


class _Works:
    async def complete(self, prompt: str) -> str:
        return "모델이 쓴 문장"


FORECAST = {
    "name": "불꽃의 대검",
    "anchor_price": 29260.0,
    "horizon_days": 7,
    "expected_change_pct": 1.37,
    "cold_start": False,
}

ANOMALY = {
    "trade_id": 23659,
    "is_anomaly": True,
    "contributions": [{"feature": "체결 가격 비율", "share": 0.62}],
}


class TestFallsBackInsteadOfRaising:
    def test_failure_produces_a_sentence_not_an_exception(self):
        answer, _, degraded = asyncio.run(
            _timed_complete(_Broken(), "프롬프트", lambda: "대체 문장")
        )
        assert answer == "대체 문장"
        assert degraded is True

    def test_success_path_is_unchanged(self):
        answer, _, degraded = asyncio.run(
            _timed_complete(_Works(), "프롬프트", lambda: "대체 문장")
        )
        assert answer == "모델이 쓴 문장"
        assert degraded is False

    def test_the_fallback_is_not_built_when_the_call_succeeds(self):
        """폴백을 **콜러블로** 받는 이유다. 값으로 받으면 성공 경로에서도 매번
        문장을 만들게 되고, 쓰지도 않을 것을 계산한다."""
        built = []

        def fallback() -> str:
            built.append(1)
            return "대체"

        asyncio.run(_timed_complete(_Works(), "프롬프트", fallback))
        assert built == []


class TestForecastSentence:
    def test_names_the_item_and_the_numbers(self):
        answer = _forecast_answer(FORECAST)
        assert "불꽃의 대검" in answer
        assert "29,260원" in answer
        assert "1.37%" in answer
        assert "7일" in answer

    def test_does_not_echo_a_query_because_it_never_sees_one(self):
        """ADR-0039 가 설명 프롬프트에서 질의를 뺀 것과 같은 보장이다 —
        시그니처가 결과만 받으므로 질의의 주어를 채택할 수 없다."""
        import inspect

        assert list(inspect.signature(_forecast_answer).parameters) == ["result"]

    @pytest.mark.parametrize(
        "change,word", [(1.37, "상승"), (-2.5, "하락"), (0.0, "보합")]
    )
    def test_direction_matches_the_sign(self, change, word):
        answer = _forecast_answer({**FORECAST, "expected_change_pct": change})
        assert word in answer

    def test_cold_start_is_disclosed(self):
        """콜드스타트인데 안 밝히면 추정치를 실측치로 읽는다.

        프롬프트가 조건부로 붙이던 문장이라, 폴백에서 빠지면 **모델이 살아
        있을 때만 정직한** 응답이 된다.
        """
        assert "추정한 값" in _forecast_answer({**FORECAST, "cold_start": True})
        assert "추정한 값" not in _forecast_answer(FORECAST)


class TestAnomalySentence:
    def test_states_the_verdict_and_the_top_factor(self):
        answer = _anomaly_answer(ANOMALY)
        assert "23659" in answer
        assert "이상 징후가 있습니다" in answer
        assert "체결 가격 비율" in answer
        assert "62%" in answer

    def test_negative_verdict_is_not_the_positive_one_negated_by_regex(self):
        """`이상 거래` 가 `이상 거래로 판별되지 않았습니다` 를 잡았던 전례가 있다.

        문장을 코드가 만드니 두 판정이 **다른 문자열**이어야 한다 — 부정문이
        긍정문을 부분문자열로 포함하면 화면·검사 어느 쪽에서도 구분이 어렵다.
        """
        yes = _anomaly_answer(ANOMALY)
        no = _anomaly_answer({**ANOMALY, "is_anomaly": False})
        assert "이상 징후가 없습니다" in no
        assert "이상 징후가 있습니다" not in no
        assert yes != no

    def test_does_not_repeat_the_disclosure_the_screen_already_makes(self):
        """합성 코퍼스 고지는 **문장이 아니라 화면과 페이로드**가 책임진다.

        고지 자체가 필요 없어진 게 아니다 — 두 id 공간은 실제로 겹치고(ADR-0022)
        `23659` 를 자기 거래 번호로 착각할 수 있다. 바뀐 것은 **어디가 그걸
        보장하는가**다. 화면은 판정 카드 아래에 무조건 적고 응답에는
        `detection.id_space` 가 실려 간다. 프롬프트의 그 줄은 중복이면서
        **셋 중 유일하게 실패할 수 있는 층**이었다.

        **프롬프트와 폴백에서 같이 뺐는지**를 여기서 고정한다. 한쪽만 빠지면
        프로바이더가 죽었을 때만 문장이 하나 더 붙어, 같은 질의가 상황에 따라
        다른 말을 한다 — ADR-0041 이 막으려던 바로 그 비대칭이다.
        """
        import inspect

        from app.services.assistant import pipeline

        for flag in (True, False):
            assert "합성 데모" not in _anomaly_answer({**ANOMALY, "is_anomaly": flag})
        assert "합성 데모" not in pipeline._ANOMALY_PROMPT, (
            "프롬프트에만 남으면 모델이 살아 있을 때만 고지가 붙는다"
        )
        # 폴백이 여전히 **알맹이**는 다 말하는지 — 지우다 같이 지운 게 없어야 한다.
        src = inspect.getsource(pipeline._anomaly_answer)
        assert "trade_id" in src and "contributions" in src

    def test_survives_an_empty_contribution_list(self):
        """`contributions()` 는 총합이 0이면 빈 목록을 준다 — 폴백이 거기서
        터지면 폴백의 의미가 없다."""
        answer = _anomaly_answer({**ANOMALY, "contributions": []})
        assert "23659" in answer and "이상 징후" in answer


class TestObservable:
    def test_degraded_is_its_own_outcome(self):
        """500 이 안 나므로 **다른 데서는 안 보인다.** 이 값이 늘면 프로바이더가
        흔들리는 것이다."""
        assert _outcome({"degraded": True}) == "degraded"

    def test_ordinary_responses_are_still_ok(self):
        assert _outcome({}) == "ok"

    def test_it_does_not_shadow_the_existing_verdicts(self):
        """플래그가 같이 설 일은 없지만 순서를 고정한다 — 나중에 하나가 다른
        하나를 가리지 않게."""
        assert _outcome({"out_of_domain": True, "degraded": True}) == "out_of_domain"
        assert _outcome({"no_results": True, "degraded": True}) == "no_results"
