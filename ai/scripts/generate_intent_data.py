"""의도 분류 학습 발화를 LLM으로 생성한다.

실행: python -m scripts.generate_intent_data
      python -m scripts.generate_intent_data --per-class 150

**학습 전용이다.** 평가셋은 손으로 쓴 `app/corpus/intent_utterances.py`를
쓴다 — 학습과 평가를 같은 LLM으로 만들면 모델이 의도를 배운 건지 그 LLM의
말투를 외운 건지 구분할 수 없다(Phase 4에서 실측, ADR-0007).

생성 결과는 저장 직전에 평가/경계 발화와의 중복을 걸러낸다. 코퍼스 모듈도
임포트 시점에 같은 검사를 하지만, 여기서 먼저 걸러야 오염된 파일이 아예
디스크에 남지 않는다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from app.corpus.intent_utterances import BOUNDARY_UTTERANCES, EVAL_UTTERANCES
from app.services.llm.dependencies import get_llm_client
from app.services.router.intents import TRAINABLE_INTENTS, Intent

OUTPUT = Path(__file__).resolve().parents[1] / "data" / "intent_train.json"

_GUIDE = {
    Intent.FAQ_SMALLTALK: (
        "인사, 잡담, 서비스 이용법·수수료·환불·약관 문의. "
        "아이템이나 가격 얘기는 절대 넣지 마세요."
    ),
    Intent.ITEM_SEARCH: (
        "아이템을 찾거나 목록을 보고 싶다는 요청. 속성·가격대·강화수치·레벨 "
        "조건이 붙어도 됩니다. 단 '시세가 얼마인지'를 묻는 건 아닙니다."
    ),
    Intent.PRICE_FORECAST: (
        "특정 아이템의 시세·적정가·가격 전망을 묻는 요청. "
        "'오를까', '사도 되나', '얼마쯤 하나' 같은 표현."
    ),
    Intent.ANOMALY_CHECK: (
        "특정 거래가 이상거래/사기인지 확인해달라는 요청. "
        "거래 번호가 있을 수도 없을 수도 있습니다."
    ),
    Intent.COMPOUND: (
        "두 가지 이상을 한 번에 요구하는 요청. 예: 아이템을 찾고 그 시세도 "
        "묻거나, 거래를 점검하면서 대체 매물도 요구하는 경우."
    ),
}

# 모호한 발화도 COMPOUND로 학습시킨다.
#
# 처음에는 확신도 임계값으로 모호함을 걸러내려 했는데, 실측해보니 분류기의
# softmax 확신도가 오답에서도 0.98을 넘어 정답과 구분되지 않았다(과적합에
# 따른 miscalibration). 임계값으로는 못 거른다.
#
# 그래서 "애매하다"를 확신도에서 유도하지 않고 **클래스로 직접 가르친다.**
# COMPOUND는 언어 형태가 아니라 "도구를 특정할 수 없거나 여러 개 필요하다"는
# 취할 행동으로 정의되므로, 모호한 발화를 여기에 넣는 것이 의미상 맞다.
#
# 수작업 경계 평가셋(BOUNDARY_UTTERANCES)은 홀드아웃이라 학습에 쓸 수 없어
# 별도로 생성한다.
_AMBIGUOUS_GUIDE = (
    "무엇을 원하는지 특정할 수 없는 아주 짧고 모호한 발화. "
    "대상 아이템이나 거래를 지칭하지 않고 지시대명사만 쓰거나, "
    "'괜찮아?', '어때?', '봐줘' 처럼 판단만 요구하는 형태."
)

_PROMPT = """당신은 게임 아이템 거래소 챗봇의 학습 데이터를 만듭니다.

아래 의도에 해당하는 한국어 사용자 발화를 {count}개 생성하세요.

의도: {label}
설명: {guide}

조건:
- 실제 게임 유저 말투로 다양하게. 반말/존댓말, 짧은 것/긴 것을 섞으세요.
- 게임 아이템은 검/활/지팡이/갑옷/장신구/물약/계정/골드 등을 자연스럽게 쓰세요.
- 서로 겹치지 않게 하세요.
- 설명 없이 JSON 배열 하나만 출력하세요. 예: ["발화1", "발화2"]"""


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--per-class", type=int, default=120)
    args = parser.parse_args()

    client = get_llm_client()
    held_out = {text.strip() for text, _ in EVAL_UTTERANCES}
    held_out |= {text.strip() for text in BOUNDARY_UTTERANCES}

    rows: list[dict[str, str]] = []
    seen: set[str] = set()

    def absorb(label: str, intent: Intent, utterances: list[str]) -> None:
        kept = 0
        for text in utterances:
            text = text.strip()
            if not text or text in seen or text in held_out:
                continue
            seen.add(text)
            rows.append({"text": text, "intent": intent.value})
            kept += 1
        print(
            f"  {label:<18} 생성 {len(utterances):>3} → 채택 {kept:>3} "
            f"(중복/홀드아웃 겹침 {len(utterances) - kept} 제외)"
        )

    for intent in TRAINABLE_INTENTS:
        raw = await client.complete(
            _PROMPT.format(
                count=args.per_class, label=intent.value, guide=_GUIDE[intent]
            )
        )
        absorb(intent.value, intent, _parse(raw))

    raw = await client.complete(
        _PROMPT.format(
            count=args.per_class,
            label="모호한 발화 (compound로 라벨링)",
            guide=_AMBIGUOUS_GUIDE,
        )
    )
    absorb("ambiguous->compound", Intent.COMPOUND, _parse(raw))

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n저장: {OUTPUT} ({len(rows)}건)")


def _parse(raw: str) -> list[str]:
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1:
        raise ValueError(f"JSON 배열을 찾을 수 없습니다: {raw[:200]}")
    return [str(item) for item in json.loads(text[start : end + 1])]


if __name__ == "__main__":
    asyncio.run(main())
