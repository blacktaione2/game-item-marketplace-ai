"""설명 프롬프트가 고쳐진 상태로 남아 있는가 (ADR-0038).

프롬프트는 코드처럼 보이지 않아서 조용히 되돌아간다. 여기서 고정하는 것은
문구가 아니라 **두 결함이 다시 생기지 않는 성질** 둘이다.

품질 자체는 이 테스트가 판정하지 않는다 — 그건
`scripts/evaluate_explanation_prompts.py` 가 LLM 을 실제로 불러서 잰다.
여기서는 API 없이 확인할 수 있는 것만 본다.
"""

import re

from app.services.assistant.pipeline import _ANOMALY_PROMPT, _FORECAST_PROMPT


class TestAnomalyPrompt:
    def test_no_longer_dictates_a_sentence_to_append(self):
        """**덧붙일 문장을 불러주는 지시 자체가 사라졌다.**

        ADR-0038 이 고친 결함은 그 지시문이 `…라는 점.` 이라는 **명사형 조각**
        이어서 모델이 글자 그대로 옮겨 붙인 것이었다(화면에서는 문장이 잘린
        것처럼 보였지만 잘린 게 아니었다). 지금은 지시가 아예 없으므로 그 결함이
        **구조적으로 불가능**하다 — ADR-0036 이 검색 설명 LLM 을 없앤 것과 같은
        방향이다.

        고지가 필요 없어진 게 아니라 **책임지는 층이 바뀌었다.** 화면이 판정 카드
        아래에 무조건 적고 응답에는 `detection.id_space` 가 실려 간다. 프롬프트의
        그 줄은 중복이면서 셋 중 유일하게 실패할 수 있는 층이었다.
        """
        assert "덧붙이세요" not in _ANOMALY_PROMPT
        assert "합성 데모" not in _ANOMALY_PROMPT
        # 조각을 불러주는 형태가 되돌아오면 잡는다.
        assert not re.search(r"라는 점\.", _ANOMALY_PROMPT), "명사형 조각이 되돌아왔다"

    def test_does_not_teach_the_model_a_field_name_as_vocabulary(self):
        """예전 판본은 `contributions는 …기여도입니다` 로 필드명을 가르쳤다.

        가르친 이름은 답변에 나온다. 지금은 "쓰지 말라"는 지시로만 등장해야 한다.
        """
        assert "contributions는" not in _ANOMALY_PROMPT
        assert "쓰지 마세요" in _ANOMALY_PROMPT


class TestForecastPrompt:
    def test_the_cold_start_branch_is_not_decided_by_the_model(self):
        """`cold_start가 true면` 이라고 조건을 걸면 모델이 그 필드를 읽어야 한다.

        읽은 이름은 문장으로 샌다 — 실제로 `cold_start가 false이므로` 가
        사용자 답변에 나왔다. 분기는 코드가 하고 완성된 지시문만 넘긴다.
        """
        assert "cold_start가 true면" not in _FORECAST_PROMPT
        assert "{conditional}" in _FORECAST_PROMPT

    def test_still_separates_baseline_from_listing_price(self):
        """고쳐지는 김에 사라지면 안 되는 것.

        기준가와 등록가를 섞어 읽는 실수는 Phase 6 에이전트에서 실제로 났고
        (`도구-출력-필드명-모호성.md`), 그 방지 문구가 이 프롬프트에 있다.
        """
        assert "등록가와 혼동하지 마세요" in _FORECAST_PROMPT
        assert "{baseline_source}" in _FORECAST_PROMPT

    def test_it_names_the_item_it_forecast(self):
        """대상을 안 밝히면 사용자는 무엇에 대한 답인지 모른다.

        `{query}` 를 뺀 것의 짝이다 — 질의를 안 주면서 대상도 안 밝히면
        "이 아이템의 최근 거래가는…" 이라는 주어 없는 문장이 된다. 실측:
        이전 판본이 도메인 밖 케이스 9건에서 **9건 다** 그랬다.
        """
        assert "아이템 이름을 답변에 그대로 밝히세요" in _FORECAST_PROMPT


class TestNeitherPromptSeesTheQuery:
    """**질의를 넘기지 않는다** (ADR-0039).

    넘기면 모델이 `result["name"]` 을 손에 쥐고도 질의의 주어를 고른다 —
    `"삼성전자 주식 어때?"` 에 `"삼성전자 주식의 최근 거래가는 약 26,090원"`
    이라고 답했고, 숫자는 다른 아이템의 진짜 예측값이었다.

    "질의의 대상을 쓰지 마세요" 라는 지시로 막지 않은 이유: 이 저장소는
    **혼동 대상을 이름으로 부르면 오히려 그쪽으로 쏠린 전례**가 있다. 자리표시자
    자체가 없으면 되풀이가 **구조적으로 불가능**하다.
    """

    def test_forecast_prompt_has_no_query_placeholder(self):
        assert "{query}" not in _FORECAST_PROMPT

    def test_anomaly_prompt_has_no_query_placeholder(self):
        assert "{query}" not in _ANOMALY_PROMPT

    def test_formatting_without_a_query_still_works(self):
        """자리표시자가 남아 있으면 `.format()` 이 KeyError 로 죽는다 —
        호출부와 프롬프트가 어긋난 채로 배포되는 걸 여기서 막는다."""
        _ANOMALY_PROMPT.format(result={"trade_id": 1})
        _FORECAST_PROMPT.format(
            result={"name": "검"}, baseline_source="최근 체결가", conditional="..."
        )
