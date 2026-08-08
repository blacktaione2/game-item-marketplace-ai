"""무속성 후처리 — ADR-0040.

**LLM 없이 전부 돈다.** 후처리는 질의와 추출 결과만으로 결정되는 함수이기
때문이다. 추출 정확도 자체는 `scripts/evaluate_element_extraction.py` 가 잰다.

여기서 고정하는 것은 세 가지다.

1. 채워야 할 때 채우는가
2. **채우면 안 될 때 안 채우는가** — 이쪽이 더 중요하다. `"검 찾아줘"` 에
   무속성이 끼면 무속성 아닌 아이템이 통째로 사라진다
3. 이미 뽑힌 값을 덮어쓰지 않는가
"""

import asyncio

import pytest

from app.corpus.element_queries import ELEMENT_QUERIES
from app.services.search.query_understanding import (
    fill_missing_element,
    understand_query,
)


class TestFillsWhatTheModelMissed:
    @pytest.mark.parametrize(
        "query",
        [
            "무속성 검 찾아줘",
            "무속성 활 있어?",
            "속성 없는 갑옷 보여줘",
            "속성없는 지팡이 추천",
            "무속성인 반지 있나",
            "무속성의 대검 찾습니다",
            "무속성 방패 5만원 이하",
        ],
    )
    def test_literal_mention_fills_in(self, query):
        assert fill_missing_element(query, None) == "무속성"


class TestDoesNotFillWhenItShouldNot:
    """**이쪽이 더 중요하다.** 잘못 채우면 맞는 결과가 통째로 사라진다."""

    @pytest.mark.parametrize(
        "query",
        [
            "검 찾아줘",
            "3만원 이하 갑옷",
            "100렙 이상 활",
            "싼 아이템 추천",
            "공격력 높은 검",
        ],
    )
    def test_no_mention_stays_none(self, query):
        assert fill_missing_element(query, None) is None

    def test_the_word_속성_alone_is_not_enough(self):
        """`속성` 이라는 낱말은 있는데 답은 `None` 이다.

        낱말로 미는 구현이 여기서 걸린다 — 이 저장소가 한국어 정규식에서 두 번,
        프롬프트에서 두 번 겪은 실패의 예방접종이다.
        """
        assert fill_missing_element("속성 좋은 무기", None) is None
        assert fill_missing_element("속성 저항 높은 갑옷", None) is None

    @pytest.mark.parametrize(
        "query",
        ["화염 저항 방어구", "냉기 저항 로브 있어?", "암흑 저항 목걸이"],
    )
    def test_resistance_queries_are_untouched(self, query):
        """저항은 별개 축이다. 그리고 **다른 속성으로 넓히지 않은 이유**이기도 하다 —
        `화염` 을 낱말로 채우면 이 무리가 통째로 깨진다."""
        assert fill_missing_element(query, None) is None


class TestNegation:
    """`"무속성 아닌 검"` 에 무속성을 채우면 **원한 것의 정반대**를 준다.

    `element` 는 동등 비교 하나뿐이라 "무속성 제외" 를 표현할 수 없다. 그래서
    할 수 있는 최선은 **필터를 안 거는 것**이다 — 알려진 한계다.
    """

    @pytest.mark.parametrize(
        "query", ["무속성 아닌 검", "무속성 말고 다른 거", "무속성 빼고 보여줘"]
    )
    def test_negated_stays_none(self, query):
        assert fill_missing_element(query, None) is None

    def test_a_bare_alternation_would_have_broken_everything(self):
        """**괄호 없는 `|` 는 부정 판정을 전체 질의로 넓힌다.**

        처음 쓴 정규식이 `무속성|속성\\s*없...아닌` 이었다. `|` 가 가장 약하게
        묶이므로 `무속성` 하나만으로 매칭되고, 그러면 **모든 무속성 질의가
        부정형으로 판정돼 후처리가 한 번도 안 걸린다.**

        증상이 "고쳤는데 아무것도 안 변했다" 라서, 이 단언이 없으면 평가 실행을
        한 번 태우고 나서야 알게 된다.
        """
        assert fill_missing_element("무속성 검 찾아줘", None) == "무속성"


class TestNeverOverwrites:
    """이미 뽑힌 값은 건드리지 않는다 — 잘 되는 것(97.5%)에 닿을 수 없게 한다."""

    def test_existing_element_wins(self):
        assert fill_missing_element("무속성 검 찾아줘", "화염") == "화염"

    def test_the_불속성_misextraction_is_a_different_defect(self):
        """`"불속성"` 이 `무속성` 으로 나오는 별개 결함(약 1%)은 여기 안 걸린다.

        `None` 이 아니라 **잘못된 값**이라 조건을 통과하지 못한다. 한 함수가 두
        결함을 고치는 척하지 않게 못박는다.
        """
        assert fill_missing_element("불속성 대검", "무속성") == "무속성"


class TestWiredIntoUnderstandQuery:
    """함수만 맞고 배선이 안 되면 아무것도 고친 게 아니다."""

    def test_applies_on_the_success_path(self):
        class _Stub:
            async def complete(self, prompt: str) -> str:
                return (
                    '{"rewritten_query": "검 소드", "filters": '
                    '{"subcategory": "검", "element": null}}'
                )

        result = asyncio.run(understand_query(_Stub(), "무속성 검 찾아줘"))
        assert result.filters.element == "무속성"

    def test_applies_on_the_fallback_path_too(self):
        """규칙이 하나여야 어디서 걸리는지 헷갈리지 않는다.

        폴백은 "필터 없는 검색" 이지만, 여기서 채우는 값은 모델의 추측이 아니라
        **질의 원문에 있는 글자**라 그 취지와 어긋나지 않는다.
        """
        class _Broken:
            async def complete(self, prompt: str) -> str:
                raise RuntimeError("업스트림 장애")

        result = asyncio.run(understand_query(_Broken(), "무속성 검 찾아줘"))
        assert result.filters.element == "무속성"
        assert result.rewritten_query == "무속성 검 찾아줘"


class TestTheUserVisibleSignal:
    """고쳐졌는지를 **화면에서 한눈에 구분할 수 있어야** 한다.

    배포 직후 이 수정이 안 걸린 것처럼 보였는데, 원인은 코드가 아니라 캐시였다
    (배포 전 응답이 재생됐다). 그때 화면의 답변 문구가 유일한 단서였다 —
    `검 조건으로` 만 있고 `무속성` 이 없었다.

    그러니 이 문구는 **진단 도구**다. 바뀌면 그 사실을 알아야 한다.
    """

    def test_the_answer_names_the_element(self):
        from app.services.assistant.pipeline import _search_answer

        answer = _search_answer({"subcategory": "검", "element": "무속성"}, 5)
        assert "무속성" in answer
        assert "검" in answer

    def test_무속성_is_not_rendered_as_an_element_suffix(self):
        """`"무속성 속성"` 이 아니라 `"무속성"` 이다 — 다른 속성과 표기가 다르다."""
        from app.services.assistant.pipeline import _search_answer

        assert "무속성 속성" not in _search_answer({"element": "무속성"}, 1)
        assert "화염 속성" in _search_answer({"element": "화염"}, 1)


class TestAgainstTheLabelledSet:
    """평가셋의 정답과 후처리 단독 동작을 맞춰본다.

    **추출이 완벽하다고 가정하지 않는다** — `element=None` 이 들어왔을 때
    후처리가 무엇을 하는지만 본다. 여기서 `미언급`·`저항`·`부정형` 무리가 하나도
    안 채워지는 것이 "전부 무속성으로 채우기" 를 막는 자리다.
    """

    def test_never_fills_the_groups_that_must_stay_none(self):
        offenders = [
            (query, group)
            for query, expected, group in ELEMENT_QUERIES
            if expected is None and fill_missing_element(query, None) is not None
        ]
        assert offenders == []

    def test_fills_most_of_the_무속성_group(self):
        """전부는 아니다 — `"속성 안 붙은 신발"` 은 일부러 남겨둔 미수록 표현이다.

        **한계를 숫자로 드러내려고** 평가셋에 넣은 것이라, 여기서도 통과시키면
        평가셋의 의도가 사라진다.
        """
        group = [q for q, want, g in ELEMENT_QUERIES if g == "무속성" and want == "무속성"]
        filled = [q for q in group if fill_missing_element(q, None) == "무속성"]
        assert len(filled) == len(group) - 1
        missed = set(group) - set(filled)
        assert missed == {"속성 안 붙은 신발"}


class TestClearsAWronglyFilledNegation:
    """**채우기만 하던 함수가 지우기도 한다** (ADR-0045).

    모델을 `gpt-5.4-mini` 로 올리니 `무속성` 미채움이 사라졌는데(69% -> 100%)
    대신 **부정형에 스스로 `무속성` 을 채우는** 새 결함이 생겼다. 채우기만 해서는
    막을 수 없다 — 이미 값이 있으니 옛 코드는 그대로 통과시켰다.

    이건 ADR-0040 이 "안 하는 것보다 훨씬 나쁘다" 고 적은 오류다: 사용자가
    **제외해달라고 한 것만** 돌려준다.
    """

    def test_부정형에_채워진_무속성을_지운다(self):
        assert fill_missing_element("무속성 빼고 보여줘", "무속성") is None
        assert fill_missing_element("무속성 아닌 검", "무속성") is None
        assert fill_missing_element("무속성 말고 다른 거", "무속성") is None

    def test_부정형이_아니면_그대로_둔다(self):
        assert fill_missing_element("무속성 검 찾아줘", "무속성") == "무속성"

    def test_다른_속성은_부정형이어도_손대지_않는다(self):
        """**이 검사가 확장의 안전선이다.**

        ADR-0040 이 "채우기만 해서 지금 잘 되는 것에 닿을 수 없다" 를 성질로
        내세웠는데, 지우기를 더하면 그 성질이 깨질 수 있다. `무속성` 일 때만
        지우므로 다른 속성은 여전히 불가침이다.
        """
        assert fill_missing_element("무속성 말고", "화염") == "화염"
        assert fill_missing_element("무속성 빼고 보여줘", "암흑") == "암흑"

    def test_평가셋의_부정형_그룹이_전부_None_이_된다(self):
        """모델이 채워 보내든 안 보내든 결과가 같아야 한다.

        `None` 을 넣은 경우는 기존 검사가 이미 보고 있고, 여기서는 **모델이
        `무속성` 을 채워 보낸 경우**를 같은 기준으로 본다.
        """
        negations = [q for q, want, g in ELEMENT_QUERIES if g == "부정형"]
        assert negations, "부정형 그룹이 비었다 - 이 검사가 공허해진다"
        assert all(fill_missing_element(q, "무속성") is None for q in negations)
