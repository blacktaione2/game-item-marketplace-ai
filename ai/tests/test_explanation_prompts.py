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
    def test_the_appended_sentence_is_a_complete_sentence(self):
        """지시문이 `…라는 점.` 이면 모델은 그 조각을 그대로 옮겨 붙인다.

        배포 화면에서 문장이 잘린 것처럼 보였던 게 그거다 — **잘린 게 아니라
        지시문이 그 모양이었다.** 모델은 시킨 대로 했다.
        """
        # "덧붙이세요:" 뒤에 오는 내용이 완결 어미로 끝나야 한다.
        appended = _ANOMALY_PROMPT.split("덧붙이세요**:")[-1]
        # 프롬프트 뒷부분(질의/결과 자리표시자)은 빼고 지시 문장만 본다.
        instruction = appended.split("질의:")[0].strip()
        assert instruction.endswith("다."), instruction
        assert not re.search(r"라는 점\.$", instruction), "명사형 조각이 되돌아왔다"

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
