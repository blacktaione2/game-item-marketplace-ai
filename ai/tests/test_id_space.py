"""식별자 공간 가드 테스트.

합성 코퍼스(trade_id 1~26,702)와 백엔드 거래(1~)는 **범위가 겹친다.** 그래서
범위 검사로는 구분할 수 없고, 호출자가 공간을 명시해야 한다. 이 테스트는 그
가드가 실제로 막는지를 확인한다 — 막히지 않으면 엉뚱한 거래의 이상 판정이
조용히 나간다.
"""

import inspect

import pytest

from app.core.ids import (
    SUPPORTED_SPACES,
    IdSpace,
    MalformedIdRefError,
    UnsupportedIdSpaceError,
    format_ref,
    parse_ref,
    require_supported,
)


def test_synthetic_is_supported():
    require_supported(IdSpace.SYNTHETIC, "거래")  # 예외가 없어야 한다


def test_backend_space_is_rejected():
    """백엔드 거래는 아직 연동돼 있지 않다. 조용히 합성 데이터로 답하면 안 된다."""
    with pytest.raises(UnsupportedIdSpaceError):
        require_supported(IdSpace.BACKEND, "거래")


def test_rejection_message_explains_the_overlap():
    """왜 대신 답해줄 수 없는지가 메시지에 있어야 고치는 사람이 이해한다."""
    with pytest.raises(UnsupportedIdSpaceError) as caught:
        require_supported(IdSpace.BACKEND, "거래")
    assert "겹" in str(caught.value)


def test_backend_is_not_yet_wired():
    """Phase 8에서 백엔드를 연동하면 이 테스트가 실패한다 — 그게 신호다.

    그때 SUPPORTED_SPACES에 BACKEND를 넣고 이 테스트를 지우면, 가드를 부르는
    모든 진입점이 한 번에 열린다.
    """
    assert IdSpace.BACKEND not in SUPPORTED_SPACES


# --- 접두사 참조 (ADR-0022) -------------------------------------------------


def test_prefix_selects_the_space():
    assert parse_ref("syn:3", "거래") == (IdSpace.SYNTHETIC, 3)
    assert parse_ref("pg:3", "거래") == (IdSpace.BACKEND, 3)


def test_same_number_is_two_different_trades():
    """이 저장소의 결함을 그대로 옮긴 테스트 — 3번은 양쪽에 다 있다."""
    synthetic, _ = parse_ref("syn:3", "거래")
    backend, _ = parse_ref("pg:3", "거래")
    assert synthetic is not backend


def test_bare_number_is_rejected_not_guessed():
    """접두사 없는 번호를 추측하는 것이 애초의 결함이다."""
    with pytest.raises(MalformedIdRefError):
        parse_ref("3", "거래")


@pytest.mark.parametrize(
    "ref", ["", ":", "syn:", ":3", "syn:abc", "foo:3", "syn:0", "syn:-1", "syn:3:4"]
)
def test_unparseable_refs_are_rejected(ref):
    with pytest.raises(MalformedIdRefError):
        parse_ref(ref, "거래")


def test_unsupported_space_parses_then_gets_501():
    """해석 불가(400)와 미연동(501)은 다른 답이어야 한다.

    `pg:3`은 **잘 만들어진 요청**이다. 요청을 고치라고 하면 안 되고, 서버가
    아직 못 한다고 해야 한다. 그래서 parse_ref는 지원 여부를 보지 않는다.
    """
    space, trade_id = parse_ref("pg:3", "거래")  # 파싱은 통과한다
    assert trade_id == 3
    with pytest.raises(UnsupportedIdSpaceError):
        require_supported(space, "거래")


def test_format_ref_roundtrips():
    for space in IdSpace:
        assert parse_ref(format_ref(space, 42), "거래") == (space, 42)


def test_detect_trade_has_no_default_id_space():
    """기본값이 있으면 인자를 빠뜨린 호출자가 조용히 합성 데이터를 받는다.

    이 함수가 막으려는 실패 방식이 바로 그것이므로, 시그니처를 고정한다.
    실제로 한동안 `id_space: IdSpace = IdSpace.SYNTHETIC` 이었다(ADR-0022).
    """
    from app.services.anomaly.pipeline import detect_trade

    parameter = inspect.signature(detect_trade).parameters["id_space"]
    assert parameter.default is inspect.Parameter.empty
