"""룰 라우터와 분기 실행 가능성 판정 테스트.

모델 없이 결정론적으로 도는 부분만 다룬다. 분류기 정확도는 단위 테스트가
아니라 `scripts.train_intent_router`의 홀드아웃 평가로 판단한다.
"""

import pytest

from app.services.assistant.pipeline import _extract_trade_id, _has_target
from app.services.router.intents import Intent
from app.services.router.rules import classify_by_rules


@pytest.mark.parametrize(
    "query,expected",
    [
        ("불속성 검 찾아줘", Intent.ITEM_SEARCH),
        ("롱소드 시세 얼마야", Intent.PRICE_FORECAST),
        ("거래 23659번 이상거래야?", Intent.ANOMALY_CHECK),
        ("안녕하세요", Intent.FAQ_SMALLTALK),
    ],
)
def test_single_signal_resolves(query, expected):
    assert classify_by_rules(query) == expected


def test_multiple_signals_become_compound():
    """두 의도가 동시에 잡히면 도구 하나로는 못 푼다는 뜻이다."""
    assert classify_by_rules("불꽃의 대검 찾아서 시세도 알려줘") is Intent.COMPOUND


def test_no_signal_abstains():
    """확신이 없으면 판정하지 않고 분류기에 넘긴다."""
    assert classify_by_rules("음") is None


def test_price_condition_is_not_a_price_inquiry():
    """'3만원 이하'는 검색 필터 조건이지 시세 문의가 아니다.

    표면에 금액이 나온다고 시세 분기로 보내면 검색 질의가 통째로 잘못 라우팅된다.
    """
    assert classify_by_rules("3만원 이하 갑옷 보여줘") is Intent.ITEM_SEARCH


class TestHasTarget:
    """시세 분기는 대상을 특정할 수 있을 때만 실행 가능하다."""

    @pytest.mark.parametrize(
        "query", ["이거 얼마?", "가격 어때", "적당한거로", "이거 뭐야"]
    )
    def test_vague_queries_have_no_target(self, query):
        assert _has_target(query) is False

    @pytest.mark.parametrize(
        "query", ["롱소드 시세", "불꽃의 대검 적정가 알려줘", "미스릴 단검 얼마"]
    )
    def test_queries_naming_an_item_have_a_target(self, query):
        assert _has_target(query) is True


class TestExtractTradeId:
    @pytest.mark.parametrize(
        "query,expected",
        [
            ("거래 23659번 확인해줘", 23659),
            ("거래 12345 이상한가", 12345),
            ("거래번호 777번", 777),
        ],
    )
    def test_extracts(self, query, expected):
        assert _extract_trade_id(query) == expected

    def test_returns_none_without_id(self):
        assert _extract_trade_id("이 거래 사기 아니야?") is None
