"""리랭커 점수 하한 산정용 질의셋 (수작업).

## 왜 필요한가

`"5만원 이하 검 찾아줘"`에 `초심자용 단궁`(활)이 섞여 나온다. 실측해보니
리랭커는 그걸 **이미 최하위로 판정**하고 있었다(−4.15, 6건 중 꼴찌). 문제는
파이프라인이 점수와 무관하게 상위 N건을 그대로 반환하는 것 — 순위는 맞는데
자르는 기준이 없다.

그래서 하한을 두려는데, **감으로 정하지 않는다.** 이 프로젝트에서 임계값을
추측으로 정했다가 뒤집힌 전례가 둘 있다.

- 오토인코더 임계값을 학습셋에서 뽑아 낙관 편향(→ 정상 3분할)
- 시맨틱 캐시 유사도 임계값(→ 함정 쌍이 동의 쌍보다 유사도가 높아 범위 축소)

## 라벨링 방식

질의별로 **"적합"의 조건을 명시적 술어로** 적는다. 아이템명이 조건을
만족하면 적합, 아니면 잘라야 할 대상이다.

`requires`는 **OR 묶음들의 AND**다. 예를 들어 `"불속성 검"`은
`(("불꽃","화염"), ("검","소드","대검","단검"))` — 속성과 종류를 **둘 다**
만족해야 한다. `불꽃의 마법봉`은 속성만 맞아서 부적합이다.

이 술어는 리랭커가 쓰는 신호가 아니다(크로스인코더는 문장 전체를 본다).
그래서 "내 라벨을 리랭커 점수가 분리하는가"는 순환논증이 아닌 측정이다.

`expect_all_fit=True`인 질의는 **음성 통제군**이다. 하한이 이 질의의 결과를
자르면 그건 하한의 비용이다 — 적합한 결과를 잃는 것이라 잡아내야 한다.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FloorQuery:
    query: str
    # OR 묶음들의 AND. 전부 만족해야 적합.
    requires: tuple[tuple[str, ...], ...]
    note: str = ""
    # 상위 결과가 전부 적합해야 하는 질의(음성 통제군)
    expect_all_fit: bool = False
    # 조건을 만족하는 아이템이 코퍼스에 아예 없는 질의
    expect_none_fit: bool = False
    # 임계값 산정에 쓸지(tune) 검증에만 쓸지(holdout).
    # 전체로 임계값을 정하고 전체로 보고하면 낙관 편향이 생긴다 — ADR-0012에서
    # 시맨틱 캐시 임계값에 같은 함정을 지적했다. 통제군과 정답 0건 질의를
    # 양쪽에 나눠 담는다.
    holdout: bool = False


_SWORD = ("검", "소드", "대검", "단검", "해머")  # 근접 무기류로 넓게 잡음
_BLADE = ("검", "소드", "대검", "단검")  # 검류만
_BOW = ("활", "궁", "쇠뇌")
_STAFF = ("지팡이", "마법봉", "마법구")
_FIRE = ("불꽃", "화염")
_ICE = ("얼음", "서리", "냉기")

FLOOR_QUERIES: list[FloorQuery] = [
    # --- 종류 혼입 (지금 실제로 발생하는 케이스) ---------------------------
    FloorQuery(
        query="5만원 이하 검 찾아줘",
        requires=((*_BLADE,),),
        note="초심자용 단궁(활)·스틸 해머가 섞여 나오는 실제 케이스",
    ),
    FloorQuery(
        query="100렙 이상 활 찾아줘",
        requires=((*_BOW,),),
        note="레벨 조건은 필터가 처리, 종류만 라벨",
    ),
    FloorQuery(
        query="마법사가 쓸 지팡이 추천",
        requires=((*_STAFF,),),
        holdout=True,
    ),
    FloorQuery(
        query="단검 보여줘",
        requires=(("단검",),),
        note="단궁과 표면이 비슷해 임베딩이 혼동하기 쉬운 질의",
        holdout=True,
    ),
    # --- 속성 + 종류 둘 다 --------------------------------------------------
    FloorQuery(
        query="불속성 검 찾아줘",
        requires=(_FIRE, _BLADE),
        note="불꽃의 마법봉은 속성만 맞아 부적합",
    ),
    FloorQuery(
        query="얼음속성 지팡이",
        requires=(_ICE, _STAFF),
        holdout=True,
    ),
    # --- 조건 만족 아이템이 코퍼스에 없음 -----------------------------------
    FloorQuery(
        query="3만원 이하 불속성 검 찾아줘",
        requires=(_FIRE, _BLADE),
        note="불속성 아이템이 전부 3만원 초과 — 정답이 0건인 질의",
        expect_none_fit=True,
    ),
    FloorQuery(
        query="2만원 이하 전설 등급 무기",
        requires=(("전설",),),
        note="전설 등급은 30만원 — 정답 0건",
        expect_none_fit=True,
        holdout=True,
    ),
    # --- 음성 통제군: 상위가 전부 적합이어야 함 -----------------------------
    FloorQuery(
        query="롱소드",
        requires=(("롱소드",),),
        note="통제군 — 하한이 이걸 자르면 비용이다",
        expect_all_fit=True,
    ),
    FloorQuery(
        query="강화 갑옷",
        requires=(("갑옷", "사슬갑", "판금"),),
        note="통제군",
        expect_all_fit=True,
        holdout=True,
    ),
]


def is_fit(item_name: str, query: FloorQuery) -> bool:
    """아이템이 질의 조건을 만족하는가 (OR 묶음들의 AND)."""
    return all(
        any(token in item_name for token in group) for group in query.requires
    )
