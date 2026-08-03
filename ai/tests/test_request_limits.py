"""요청 크기 상한 (ADR-0035).

## 왜 이게 비용 방어의 일부인가

한도 3계층(토큰 요구 · 20회/분 · 50회/일)은 전부 **요청 수**를 센다. 요청 하나의
길이에 상한이 없으면 **하루 예산이 그 배수만큼 늘어난다** — 한도를 지키면서.

실측으로 확인한 수정 전 상태: 19,800자 질의가 **200 으로 통과**했고 16.4초 걸렸다.
정상 질의는 ~50자 / 4.5초다.

## 상한을 500자로 둔 근거

같은 DTO 의 `size` 는 이미 `ge=1, le=50` 으로 묶여 있었다 — **크기는 묶고 길이는
빠뜨린 대비**가 이 결함의 발견 단서였다. 숫자는 둘로 뒷받침된다.

1. **파이프라인이 그 너머를 보지도 않는다.** 임베딩 `max_seq_length` 는 128토큰,
   리랭커는 256토큰에서 자른다. 그 뒤의 텍스트는 캐시·분류기·리랭킹 어디에도
   영향을 주지 않고 **LLM 요금만 늘린다**
2. **데이터셋 질의 547건의 최댓값이 33자다**(중앙 18, p99 31). 500자는 15배 여유다

## 여기서 재는 것

**경계값이다.** 상한이 있다는 것만 확인하면 off-by-one 을 놓친다 — 500 은 통과하고
501 은 거부돼야 한다. 빈 문자열도 막는다(`min_length=1`): 빈 질의는 LLM 을 부를
이유가 없는데 예전에는 그대로 파이프라인에 들어갔다.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.routers.assistant import AssistantRequest
from app.routers.search import SearchRequest

MAX = 500


@pytest.mark.parametrize("model", [AssistantRequest, SearchRequest])
class TestQueryLength:
    def test_accepts_the_boundary(self, model):
        """정확히 상한이면 통과한다 — 상한을 하나 낮게 잡는 실수를 잡는다."""
        assert len(model(query="가" * MAX).query) == MAX

    def test_rejects_one_over(self, model):
        """하나 넘으면 거부한다. 이게 실제 방어선이다."""
        with pytest.raises(ValidationError):
            model(query="가" * (MAX + 1))

    def test_rejects_the_measured_attack_size(self, model):
        """실측에서 200 으로 통과했던 19,800자."""
        with pytest.raises(ValidationError):
            model(query="find me a cheap sword " * 900)

    def test_rejects_empty(self, model):
        """빈 질의로 LLM 을 부를 이유가 없다."""
        with pytest.raises(ValidationError):
            model(query="")

    def test_normal_queries_are_unaffected(self, model):
        """데이터셋 최댓값이 33자다 — 정상 사용은 상한 근처에도 안 간다."""
        for query in ["5만원 이하 검 찾아줘", "100렙 이상 활", "+9 롱소드 시세 알려줘"]:
            assert model(query=query).query == query


class TestSizeStillBounded:
    """`size` 상한은 원래 있었다. **같이 재는 이유는 회귀 방지다** — 이번 수정이
    `query` 만 건드렸다는 것을 고정한다."""

    def test_size_upper_bound(self):
        with pytest.raises(ValidationError):
            SearchRequest(query="검", size=51)

    def test_size_lower_bound(self):
        with pytest.raises(ValidationError):
            SearchRequest(query="검", size=0)
