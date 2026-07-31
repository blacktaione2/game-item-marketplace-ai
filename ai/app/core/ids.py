"""식별자 공간(id space) 정책.

## 왜 필요한가

이 시스템에는 같은 이름의 식별자를 쓰는 **서로 다른 두 데이터 평면**이 있다.

| 공간 | 소유 | trade_id | user_id |
|---|---|---|---|
| `synthetic` | `ai/app/corpus` (인메모리 합성 데모) | 1 ~ 26,702 | 1 ~ 206 |
| `backend` | PostgreSQL (Spring Boot) | 1 ~ (증가) | 1 ~ 5 |

**두 공간의 id 범위가 겹친다.** `trade_id=3`은 양쪽 모두에서 유효한데 전혀
다른 거래를 가리킨다. 그래서 범위 검사로는 구분할 수 없고, 검사 없이 받으면
**엉뚱한 거래의 이상 판정이 조용히 돌아간다** — 에러도 안 난다.

아이템은 시딩(`scripts/export_demo_sql.py`)으로 두 공간을 같은 id로 맞췄지만,
유저와 거래는 여전히 갈라져 있다. 근본 해결(네임스페이스 분리 또는 완전 통합)은
Phase 8 과제다.

## 그때까지의 안전장치

**호출자가 어느 공간인지 명시하게 한다.** 추측하지 않는다. 지원하지 않는
공간을 지목하면 조용히 다른 답을 주는 대신 시끄럽게 거부한다.

정책(어느 공간이 실제로 연동돼 있는가)을 여기 한 곳에 둔 이유는, Phase 8에서
백엔드 거래를 연동할 때 `SUPPORTED_SPACES`에 한 줄 추가하면 모든 진입점의
가드가 동시에 풀리게 하기 위해서다.
"""

from __future__ import annotations

from enum import Enum


class IdSpace(str, Enum):
    """식별자가 속한 데이터 평면."""

    # ai/app/corpus 가 생성한 합성 데모 데이터. 이상탐지 학습·평가의 기반이다.
    SYNTHETIC = "synthetic"
    # PostgreSQL의 실제 거래·유저. Spring Boot가 소유한다.
    BACKEND = "backend"


# 현재 AI 파이프라인이 실제로 조회할 수 있는 공간.
# Phase 8에서 백엔드 거래를 연동하면 여기에 BACKEND를 추가한다 — 그러면
# require_supported()를 부르는 모든 진입점이 한 번에 열린다.
SUPPORTED_SPACES: frozenset[IdSpace] = frozenset({IdSpace.SYNTHETIC})


class UnsupportedIdSpaceError(RuntimeError):
    """아직 연동되지 않은 식별자 공간을 지목했을 때."""

    def __init__(self, space: IdSpace, entity: str) -> None:
        self.space = space
        super().__init__(
            f"'{space.value}' 공간의 {entity} 조회는 아직 지원하지 않습니다. "
            f"현재 지원: {', '.join(sorted(s.value for s in SUPPORTED_SPACES))}. "
            "합성 데모 데이터와 실제 거래는 id 범위가 겹쳐서 서로를 대신할 수 "
            "없습니다."
        )


def require_supported(space: IdSpace, entity: str) -> None:
    """지원하지 않는 공간이면 즉시 거부한다.

    id를 받는 모든 진입점이 이걸 통과시켜야 한다. 조용한 오답보다 시끄러운
    에러가 낫다는 것이 이 함수의 존재 이유다.
    """
    if space not in SUPPORTED_SPACES:
        raise UnsupportedIdSpaceError(space, entity)


# --- 접두사 표기 (Phase 8) -------------------------------------------------
#
# 외부에서 들어오는 id는 **공간을 자기가 들고 온다.** `"syn:3"`은 합성 코퍼스
# 3번, `"pg:3"`은 PostgreSQL 3번이다. 둘 다 유효하고 서로 다른 거래이므로,
# 접두사가 없는 `"3"`은 해석하지 않고 거부한다 — 추측이 바로 이 모듈이 막으려는
# 결함이다.
#
# 내부는 정수를 유지한다. 합성 id는 `index + 1`로 매겨지는 **배열 위치**이고
# (`corpus/trades.py`, `anomaly/dataset.py`), 특징 계산에서는 묶음 키로만
# 쓰인다 — 모델은 id를 값으로 본 적이 없다. 위치 인덱스를 문자열로 바꿔봐야
# 정체성이 되지 않으므로 변환은 경계에서만 한다. ADR-0022 참고.

_PREFIX_BY_SPACE: dict[IdSpace, str] = {
    IdSpace.SYNTHETIC: "syn",
    IdSpace.BACKEND: "pg",
}
_SPACE_BY_PREFIX: dict[str, IdSpace] = {
    prefix: space for space, prefix in _PREFIX_BY_SPACE.items()
}


class MalformedIdRefError(ValueError):
    """접두사가 없거나 해석할 수 없는 참조."""

    def __init__(self, ref: str, entity: str) -> None:
        self.ref = ref
        prefixes = ", ".join(f"{p}:1" for p in sorted(_SPACE_BY_PREFIX))
        super().__init__(
            f"{entity} 참조 '{ref}'를 해석할 수 없습니다. "
            f"공간 접두사가 필요합니다 (예: {prefixes}). "
            "합성 데모 데이터와 실제 거래는 id 범위가 겹치므로, 접두사 없는 "
            "번호는 어느 쪽인지 알 수 없습니다."
        )


def parse_ref(ref: str, entity: str) -> tuple[IdSpace, int]:
    """`"syn:3"` → `(SYNTHETIC, 3)`.

    지원 여부는 보지 않는다 — `"pg:3"`은 정상적으로 파싱되고, 그다음
    `require_supported()`가 501로 막는다. **해석 불가(400)와 미연동(501)은
    다른 답이어야 한다.**
    """
    prefix, separator, raw = ref.partition(":")
    if not separator or prefix not in _SPACE_BY_PREFIX:
        raise MalformedIdRefError(ref, entity)
    if not raw.isdigit() or int(raw) < 1:
        raise MalformedIdRefError(ref, entity)
    return _SPACE_BY_PREFIX[prefix], int(raw)


def format_ref(space: IdSpace, value: int) -> str:
    """`(SYNTHETIC, 3)` → `"syn:3"`."""
    return f"{_PREFIX_BY_SPACE[space]}:{value}"
