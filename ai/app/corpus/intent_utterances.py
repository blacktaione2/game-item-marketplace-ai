"""의도 분류 발화 코퍼스.

## 학습은 LLM 생성, 평가는 수작업 — Phase 4에서 배운 것

실사용 로그가 없어 학습 데이터는 LLM 생성이 불가피하다. 하지만 **평가셋까지
LLM으로 만들면 "말투를 외운 것"과 "의도를 배운 것"을 구분할 수 없다.**
Phase 4에서 이걸 실측했다 — 수동 작성 평가셋의 개선폭이 LLM 생성 평가셋의
70%에 그쳤고, 그 차이가 곧 스타일 적응분이었다(ADR-0007).

그래서 아래 평가셋은 전부 손으로 썼다. LLM이 잘 만들지 않는 형태를 일부러
넣었다: 초성(`ㅎㅇ`, `ㄱㅅ`), 조사 생략(`사용법좀`), 축약 말투(`~임?`),
띄어쓰기 붕괴, 은어(`먹튀`).

## 경계 발화

`BOUNDARY_UTTERANCES`는 정답 클래스가 하나로 안 정해지는 발화다. 목표 동작은
"맞히는 것"이 아니라 **COMPOUND/UNKNOWN으로 빠져 에이전트에게 넘어가는 것**
이다. 라우터가 이걸 억지로 한 클래스로 확신하면 그게 오히려 실패다.

학습 데이터는 `data/intent_train.json`에 있고 `scripts.generate_intent_data`로
만든다.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.services.router.intents import Intent

_DATA_FILE = Path(__file__).resolve().parents[2] / "data" / "intent_train.json"


# --- 평가셋 (전부 수작업) ---------------------------------------------------
EVAL_UTTERANCES: list[tuple[str, Intent]] = [
    # FAQ / 스몰토크
    ("안녕하세요", Intent.FAQ_SMALLTALK),
    ("ㅎㅇ", Intent.FAQ_SMALLTALK),
    ("여기 뭐하는데임?", Intent.FAQ_SMALLTALK),
    ("이용방법 알려주세요", Intent.FAQ_SMALLTALK),
    ("고마워요", Intent.FAQ_SMALLTALK),
    ("환불 되나요", Intent.FAQ_SMALLTALK),
    ("사용법좀", Intent.FAQ_SMALLTALK),
    ("님 누구세요", Intent.FAQ_SMALLTALK),
    ("약관 어디서 봄", Intent.FAQ_SMALLTALK),
    ("고객센터 연락처 좀", Intent.FAQ_SMALLTALK),
    ("ㄱㅅ", Intent.FAQ_SMALLTALK),
    ("수수료 정책이 어떻게 되나요", Intent.FAQ_SMALLTALK),
    # 아이템 검색
    ("롱소드 있음?", Intent.ITEM_SEARCH),
    ("불속성 검 추천좀", Intent.ITEM_SEARCH),
    ("100렙 이상 활 찾아줘", Intent.ITEM_SEARCH),
    ("3만원 이하 갑옷 보여줘", Intent.ITEM_SEARCH),
    ("마법사 지팡이 매물", Intent.ITEM_SEARCH),
    ("강화된 무기 목록", Intent.ITEM_SEARCH),
    ("싼 소모품 뭐가있어", Intent.ITEM_SEARCH),
    ("미스릴단검 검색", Intent.ITEM_SEARCH),
    ("궁수용 장비 추천", Intent.ITEM_SEARCH),
    ("+9 강화 무기 리스트", Intent.ITEM_SEARCH),
    ("저렙 초보템 있나요", Intent.ITEM_SEARCH),
    ("경매 나온 아이템 보여줘", Intent.ITEM_SEARCH),
    # 시세 문의
    ("롱소드 시세 얼마야", Intent.PRICE_FORECAST),
    ("불꽃의 대검 적정가 알려줘", Intent.PRICE_FORECAST),
    ("이거 오를까?", Intent.PRICE_FORECAST),
    ("지금 팔아도 되나", Intent.PRICE_FORECAST),
    ("가격 전망 어때", Intent.PRICE_FORECAST),
    ("+9 롱소드 시세추이", Intent.PRICE_FORECAST),
    ("미스릴 단검 얼마정도해요", Intent.PRICE_FORECAST),
    ("사도 괜찮은 가격임?", Intent.PRICE_FORECAST),
    ("시가 확인좀", Intent.PRICE_FORECAST),
    ("앞으로 떨어질까요", Intent.PRICE_FORECAST),
    ("행운의 목걸이 시세", Intent.PRICE_FORECAST),
    ("값 오를 것 같음?", Intent.PRICE_FORECAST),
    # 이상거래 확인
    ("거래 23659번 이상거래야?", Intent.ANOMALY_CHECK),
    ("이 거래 사기 아니야?", Intent.ANOMALY_CHECK),
    ("수상한 거래 같은데", Intent.ANOMALY_CHECK),
    ("정상 거래인지 확인해줘", Intent.ANOMALY_CHECK),
    ("이상한 거래 걸러줘", Intent.ANOMALY_CHECK),
    ("사기당한 것 같아요", Intent.ANOMALY_CHECK),
    ("거래 12345번 의심돼", Intent.ANOMALY_CHECK),
    ("이거 사기임?", Intent.ANOMALY_CHECK),
    ("비정상 거래 체크", Intent.ANOMALY_CHECK),
    ("의심스러운 거래 확인", Intent.ANOMALY_CHECK),
    ("먹튀 아닌지 봐줘", Intent.ANOMALY_CHECK),
    ("거래내역 이상 없는지", Intent.ANOMALY_CHECK),
    # 복합 질의
    ("불꽃의 대검 찾아서 시세도 알려줘", Intent.COMPOUND),
    ("롱소드 매물 보여주고 오를지도 알려줘", Intent.COMPOUND),
    ("이 검 적정가고 사기 아니야?", Intent.COMPOUND),
    ("싼 갑옷 찾아서 가격 전망까지", Intent.COMPOUND),
    ("활 추천해주고 시세도", Intent.COMPOUND),
    ("거래 확인하고 비슷한 매물도 보여줘", Intent.COMPOUND),
    ("+9 무기 목록이랑 시세 같이", Intent.COMPOUND),
    ("지팡이 찾아주고 사도 되는지 봐줘", Intent.COMPOUND),
    ("이상거래인지 보고 대체 매물 추천", Intent.COMPOUND),
    ("미스릴 단검 시세랑 이상거래 여부", Intent.COMPOUND),
    ("무기 검색해서 제일 싼거 시세 알려줘", Intent.COMPOUND),
    ("장신구 매물이랑 가격 추이 둘 다", Intent.COMPOUND),
]

# --- 경계 발화 (정답이 하나로 안 정해짐 → 에이전트로 빠져야 함) --------------
BOUNDARY_UTTERANCES: list[str] = [
    "이거 적정가야?",
    "이 아이템 어때?",
    "살만해?",
    "괜찮은거 있어?",
    "이거 얼마?",
    "좋은 거 알려줘",
    "이거 믿을만해?",
    "추천 좀",
    "어떻게 생각해?",
    "봐줘",
    "확인 좀",
    "이거 뭐야",
    "가격 어때",
    "괜찮나요",
    "이거 살까 말까",
    "골라줘",
    "뭐가 나아?",
    "이거 문제없지?",
    "적당한거로",
    "판단 좀 해줘",
]


def load_train_utterances() -> list[tuple[str, Intent]]:
    """LLM으로 생성한 학습 발화. 없으면 빈 리스트."""
    if not _DATA_FILE.exists():
        return []
    payload = json.loads(_DATA_FILE.read_text(encoding="utf-8"))
    return [(row["text"], Intent(row["intent"])) for row in payload]


def _assert_no_leak() -> None:
    """학습 발화가 평가/경계 발화와 겹치면 즉시 터뜨린다.

    Phase 4 코퍼스와 같은 장치다. 평가셋 오염은 조용히 일어나 수치만
    부풀리므로, 생성 스크립트를 다시 돌리다 겹치면 그 자리에서 알아야 한다.
    """
    train = {text.strip() for text, _ in load_train_utterances()}
    if not train:
        return

    held_out = {text.strip() for text, _ in EVAL_UTTERANCES}
    held_out |= {text.strip() for text in BOUNDARY_UTTERANCES}

    overlap = train & held_out
    if overlap:
        raise ValueError(f"학습 발화가 평가셋과 겹칩니다: {sorted(overlap)[:10]}")


_assert_no_leak()
