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
