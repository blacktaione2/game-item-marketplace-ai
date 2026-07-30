"""식별자 공간 가드 테스트.

합성 코퍼스(trade_id 1~26,702)와 백엔드 거래(1~)는 **범위가 겹친다.** 그래서
범위 검사로는 구분할 수 없고, 호출자가 공간을 명시해야 한다. 이 테스트는 그
가드가 실제로 막는지를 확인한다 — 막히지 않으면 엉뚱한 거래의 이상 판정이
조용히 나간다.
"""

import pytest

from app.core.ids import (
    SUPPORTED_SPACES,
    IdSpace,
    UnsupportedIdSpaceError,
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
