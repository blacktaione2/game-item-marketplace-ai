"""설명 프롬프트 A/B/C — 내부 필드명 노출과 미완성 문장을 고칠 수 있는가.

실행: python -m scripts.evaluate_explanation_prompts

## 고치려는 것

배포된 화면에서 눈으로 잡힌 두 건이다.

| 분기 | 나온 문장 |
|---|---|
| 시세 | `... 이 예측은 최근 120일간의 거래 이력을 바탕으로 하였으며, **Cold Start 상태가 아닙니다**.` |
| 이상거래 | `... 사용자의 실제 거래 내역과는 **번호 체계가 별개라는 점.**` |

첫째는 내부 필드명(`cold_start`)이 사용자 문장에 그대로 나온 것이고, 둘째는
**잘린 게 아니라** 프롬프트의 템플릿 문장 자체가 `…라는 점.` 이라는 명사형
조각이어서 모델이 그대로 옮겨 붙인 것이다.

## 왜 눈으로 고치지 않는가

이 저장소는 프롬프트 한 줄 추가가 정답률을 **97.5% → 22%** 로 떨어뜨린 적이
있다. 그때 원인은 "혼동하지 말라"며 **혼동 대상을 이름으로 불렀기** 때문이었다.

그래서 **"필드명을 쓰지 마세요"라고 명시하는 안(B) 자체가 같은 함정일 수
있다.** 필드명을 아예 언급하지 않는 안(C)을 같이 재는 이유다.

## 측정 설계

**한 실행 안에서 A/B/C.** 이전 실행의 집계와 비교하면 LLM 변동이 델타를
오염시킨다(`evaluate_hard_filters`가 같은 이유로 그렇게 한다).

**도구 결과는 케이스당 한 번만 계산하고 세 안이 공유한다.** 안마다 다시
계산하면 예측·판정 자체의 변동이 프롬프트 차이로 잡힌다.

**질의당 반복한다.** `temperature=0`이어도 1/10은 흔들린다(ADR-0017).

**LLM 판정을 쓰지 않는다.** 아래 넷은 전부 문자열·숫자 규칙이라 판정이
실행마다 달라지지 않는다.

| 지표 | 판정 |
|---|---|
| **누출** | 답변에 내부 식별자(`cold_start`, `baseline_price`, `contributions` …)가 등장 |
| **미완결** | 답변이 종결어미(`다.` `요.` 등)나 `?`/`!`로 끝나지 않음 |
| **면책 누락** | 이상거래 답변에 합성 데이터 고지가 없음 / 콜드스타트인데 추정치임을 안 밝힘 |
| **모순** | 판정이 이상인데 "정상"이라 말함(또는 그 반대) |

**앞의 셋은 낮을수록 좋고, 면책은 "고치다 사라지면 회귀"라 같이 본다.**
이 넷이 모두 나빠지지 않으면서 누출·미완결이 0에 가까워야 채택한다.
"""

from __future__ import annotations

import asyncio
import io
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from elasticsearch import AsyncElasticsearch

from app.core.config import get_settings
from app.core.ids import IdSpace
from app.services.anomaly.pipeline import detect_trade
from app.services.forecast.pipeline import forecast_price
from app.services.llm.openai_client import OpenAIClient

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TENANT = "nexon"
REPEATS = 3
NL = "\n"

# **수집과 채점을 분리한다.** 첫 판본은 부르면서 바로 채점하고 답변을 버렸다.
# 그런데 모순 지표가 틀린 게 드러났을 때 고쳐서 다시 보려면 **99회를 다시
# 태워야 했다.** 지표는 앞으로도 틀릴 수 있으므로 답변을 남긴다 —
# 같은 데이터 위에서 채점을 고치는 데는 추가 비용이 없다.
ANSWERS = Path(__file__).resolve().parents[1] / "data" / "explanation_prompt_answers.json"

# 시세: 콜드스타트인 것과 아닌 것이 섞여야 한다 — 두 경로의 지시가 다르다.
FORECAST_ITEMS = [1, 3, 5, 7, 24]
# 이상거래: **양쪽이 섞여야 모순 지표가 의미를 갖는다.**
#
# 이상 판정만 모으면 "이상이라고 말했는가"만 재게 되고, 정상만 모으면 그 반대다.
# 앞의 셋은 `list_alerts` 상위(전부 이상 판정), 뒤의 셋은 정상 구간에서 골랐다.
# 처음엔 이상 1건 + 정상 4건이었는데 그러면 "이상인데 정상이라 말함" 방향의
# 표본이 3회짜리 하나뿐이라 그 지표가 사실상 안 돈다.
ANOMALY_TRADES = [23659, 23673, 23663, 1, 500, 20000]

# **도메인 밖 질의 × 진짜 예측 결과** — 관측된 결함을 그대로 재현한 케이스다.
#
# 게이트(ADR-0039)가 막긴 하지만 그것도 LLM 판정이라 100%가 아니다. 뚫렸을 때
# 설명이 두 번째 방어선이 되는지를 이 케이스가 잰다.
#
# 세 번째 값은 **답변에 나오면 안 되는 고유명사**다. 고유명사를 고른 이유는
# 이 저장소가 한국어 어미 때문에 지표를 두 번 틀렸기 때문이다 — `이상 거래`가
# `이상 거래로 판별되지 않았습니다`를 잡았고, `거래를 고려`가 `거래를 고려하실
# 때`를 잡았다. `삼성전자`는 어미가 붙지 않고, 정상 답변에는 등장할 길이 없다.
OOD_FORECAST_CASES = [
    (1, "삼성전자 주식 어때?", "삼성전자"),
    (3, "비트코인 지금 사도 될까", "비트코인"),
    (24, "서울 아파트 전세 얼마야", "아파트"),
]

# --- 지표 ------------------------------------------------------------------
#
# 답변에 나오면 안 되는 내부 식별자. **응답 JSON의 키 이름들이다** — 사용자는
# 그 구조를 모르므로 문장에 나오면 그건 새어나온 것이다.
LEAK_TOKENS = [
    "cold_start", "baseline_price", "baseline_source", "anchor_price",
    "expected_change_pct", "horizon_days", "history_days", "inherited_from",
    "contributions", "is_anomaly", "anomaly_score", "price_ratio",
    "market_median", "id_space", "trade_id", "item_id", "injected_label",
]
# 종결어미. 한국어 평서문이 여기서 끝나지 않으면 문장이 안 닫힌 것이다.
_COMPLETE = re.compile(r"(다|요|죠|까)[.!?]$|[?!]$")


def leaked(answer: str) -> list[str]:
    low = answer.lower()
    return [token for token in LEAK_TOKENS if token in low]


def incomplete(answer: str) -> bool:
    return not _COMPLETE.search(answer.strip())


def missing_disclaimer(answer: str, kind: str, result: dict[str, Any]) -> bool:
    # agent 도 forecast 와 같은 규칙을 받는다 — 콜드스타트면 추정임을 밝혀야 한다.
    if kind == "anomaly":
        # 합성 데모 데이터라는 고지. 표현은 자유롭게 두되 두 요소를 요구한다.
        return not (re.search(r"합성|데모", answer) and re.search(r"데이터|거래", answer))
    if result.get("cold_start"):
        # 콜드스타트면 "직접 이력이 아니라 빌려온 추정"이라는 사실이 있어야 한다.
        return not re.search(r"유사|비슷한|이력이 (부족|없)|추정", answer)
    return False


# **부정을 읽어야 한다.** 첫 판본은 `이상\s*거래` 만 봤는데, 정상 거래 답변이
# "이상 거래로 판별되지 **않았습니다**" 라고 쓰므로 전부 오탐이 났다 — 세 안이
# 나란히 9를 낸 것이 그 증거였다. **서로 다른 프롬프트가 같은 값을 내면 그건
# 프롬프트가 아니라 검출기를 재고 있다는 뜻이다.**
_ANOMALY_WORD = r"(?:이상\s*거래|이상\s*징후|비정상)"
_NEGATOR = r"(?:아니|않|없|해당하지)"
# 이상 언급 뒤 30자 안에 부정어가 오면 "이상이 아니다"로 읽는다.
_NEGATED = re.compile(_ANOMALY_WORD + r"[^.。]{0,30}?" + _NEGATOR)
_ASSERTED = re.compile(_ANOMALY_WORD)
_NORMAL = re.compile(r"정상(?:적인)?\s*(?:거래|범위)|문제\s*(?:가)?\s*없")


# **이 지표는 나중에 붙었다.** 처음 넷으로 A/B/C 를 재니 B 와 C 가 0-0-0-0 으로
# 동점이었는데, 표본을 읽어보니 C 가 "거래를 진행하는 것이 좋습니다" 같은 **투자
# 권유**를 하고 있었다. `상담원` 페르소나가 모델을 그쪽으로 민 것이다.
#
# 시세 설명의 일은 예측을 전달하는 것이지 사라 말라를 권하는 게 아니다. 지표는
# **내가 생각한 것만 잰다** — 동점이 나오면 그건 "차이가 없다"가 아니라 "내
# 지표가 차이를 못 본다"일 수 있다.
# **"권유"와 "유보"를 갈라야 한다.** 첫 판본은 `거래를? ?고려` 를 넣었다가
# "거래를 **고려하실 때** 이 점을 **참고하시기 바랍니다**" 를 권유로 잡았다.
# 그건 사라 말라가 아니라 판단을 사용자에게 넘기는 문장이다 — 오히려 바람직하다.
#
# 잡아야 하는 건 **행동을 추천하는 서술어**다. 앞의 명사가 아니라 뒤의 어미가
# 권유를 만든다.
_ADVICE = re.compile(
    r"(?:하|사|팔|구매하|판매하|거래하)(?:는|시는) 것이 좋"
    r"|추천(?:합니다|드립니다|해)"
    r"|권(?:장합니다|해드립니다|장드립니다)"
    r"|(?:구매|매수|매도|판매)(?:를|하기)? ?(?:추천|권장)"
    r"|(?:지금이|이럴 때) ?(?:기회|적기)"
    r"|유리(?:합니다|할 것)"
)


def advises(answer: str) -> bool:
    return bool(_ADVICE.search(answer))


def adopts_subject(answer: str, subject: str) -> bool:
    """답변이 **질의의 주어를 예측 대상으로 받아 적었는가** (ADR-0039).

    도메인 밖 케이스에서만 의미가 있다. 정상 케이스는 `subject` 가 비어 있어
    항상 False 다 — 그래서 이 열의 분모는 다른 열과 다르다.
    """
    return bool(subject) and subject in answer


def omits_item_name(answer: str, item_name: str) -> bool:
    """답변이 **어느 아이템 얘기인지 밝히지 않았는가.**

    `adopts_subject` 의 짝이다. 주어를 안 받아 적더라도 대상을 아예 말하지
    않으면 사용자는 여전히 무엇에 대한 답인지 모른다 — "고치면서 다른 걸 잃는"
    쪽을 잡기 위해 같이 잰다.

    이름 전체(`+9 강화 롱소드`)를 요구하지는 않는다. 모델이 `롱소드는 …` 처럼
    줄여 쓰는 것은 정상이므로, 두 글자 이상 토큰 하나만 있으면 밝힌 것으로 본다.
    """
    if not item_name:
        return False
    return not any(token in answer for token in item_name.split() if len(token) >= 2)


def contradicts(answer: str, kind: str, result: dict[str, Any]) -> bool:
    if kind != "anomaly":
        return False
    negated = bool(_NEGATED.search(answer))
    says_anomaly = bool(_ASSERTED.search(answer)) and not negated
    says_normal = bool(_NORMAL.search(answer)) or negated
    if result["is_anomaly"]:
        return says_normal and not says_anomaly
    return says_anomaly and not says_normal


# --- 프롬프트 세 안 --------------------------------------------------------
#
# A = 현재. B = 필드명을 쓰지 말라고 **명시**. C = 필드명을 **언급하지 않음**.
#
# B 와 C 를 나눈 이유가 이 라운드의 요점이다. "혼동하지 말라"며 혼동 대상을
# 이름으로 부르는 것이 역효과였던 전례(97.5% → 22%)가 있어서, B 가 오히려
# 누출을 늘릴 가능성을 배제할 수 없다.

_FORECAST_A = """다음은 아이템 시세 예측 결과입니다. 2~3문장으로 설명하세요.

- baseline_price는 {baseline_source}를 기준으로 한 값입니다. 등록가와 혼동하지 마세요.
- cold_start가 true면 실제 거래 이력이 부족해 유사 아이템 추세를 물려받은
  추정치라는 점을 반드시 밝히세요.

질의: {query}
결과: {result}"""

_FORECAST_B = """다음은 아이템 시세 예측 결과입니다. 사용자에게 2~3문장으로 설명하세요.

- 아래 결과는 내부 데이터입니다. **필드 이름(cold_start, baseline_price 등)을
  답변에 쓰지 마세요.** 사용자는 그 구조를 모릅니다.
- 기준가는 {baseline_source}입니다. 판매자가 정한 등록가와 혼동하지 마세요.
- {conditional}
- 완결된 문장으로 끝내세요.

질의: {query}
결과: {result}"""

_FORECAST_C = """당신은 게임 아이템 거래소의 상담원입니다. 아래 시세 분석을 보고
손님에게 2~3문장으로 설명하세요.

- 기준가는 {baseline_source}입니다. 판매자가 정한 등록가와 혼동하지 마세요.
- {conditional}
- 손님은 분석 도구를 본 적이 없습니다. 도구가 쓰는 표현이 아니라 사람이 쓰는
  말로 설명하세요.
- 완결된 문장으로 끝내세요.

질의: {query}
분석: {result}"""

_ANOMALY_A = """다음은 거래 이상 여부 판정 결과입니다. 2~3문장으로 설명하세요.
contributions는 이상 점수에 대한 피처별 기여도입니다. 가장 큰 기여 요인을
근거로 들어 설명하세요.

**반드시 한 문장으로 덧붙이세요**: 이 판정은 합성 데모 거래 데이터를 대상으로
하며, 사용자의 실제 거래 내역과는 번호 체계가 별개라는 점.

질의: {query}
결과: {result}"""

_ANOMALY_B = """다음은 거래 이상 여부 판정 결과입니다. 2~3문장으로 설명하세요.
가장 큰 기여 요인을 근거로 들어 설명하세요.

- 아래 결과는 내부 데이터입니다. **필드 이름(contributions, is_anomaly 등)을
  답변에 쓰지 마세요.**
- **마지막에 다음 내용을 완결된 한 문장으로 덧붙이세요**: 이 판정은 합성 데모
  거래 데이터를 대상으로 한 것이며, 사용자의 실제 거래 번호와는 체계가 다릅니다.

질의: {query}
결과: {result}"""

_ANOMALY_C = """당신은 게임 아이템 거래소의 이상거래 담당자입니다. 아래 분석을
보고 2~3문장으로 설명하세요.

- 이상 점수에 가장 크게 기여한 요인을 근거로 드세요.
- **마지막에 다음 내용을 완결된 한 문장으로 덧붙이세요**: 이 판정은 합성 데모
  거래 데이터를 대상으로 한 것이며, 사용자의 실제 거래 번호와는 체계가 다릅니다.
- 상대는 분석 도구를 본 적이 없습니다. 도구가 쓰는 표현이 아니라 사람이 쓰는
  말로 설명하세요.

질의: {query}
분석: {result}"""


# --- D: 질의를 아예 넘기지 않는다 (ADR-0039 라운드) --------------------------
#
# 배포된 화면에서 `"삼성전자 주식 어때?"` 가 시세 분기를 타고 **"삼성전자 주식의
# 최근 거래가는 약 26,090원"** 이라고 답했다. 숫자는 `게임 머니 1000만 골드` 의
# 진짜 예측값이었다 — 모델은 `result["name"]` 을 손에 쥐고도 **질의의 주어를
# 골랐다.**
#
# 도메인 게이트가 이걸 막지만 게이트도 LLM 판정이라 100% 가 아니다. 게이트가
# 뚫렸을 때 **설명이 두 번째 방어선이 되는가**를 여기서 잰다.
#
# B 에 "질의의 대상을 그대로 쓰지 마세요" 를 한 줄 더하는 안도 있었지만, 이
# 저장소는 **혼동 대상을 이름으로 부르면 오히려 그쪽으로 쏠린 전례**가 있다
# (97.5% → 22%). `{query}` 를 안 넘기면 되풀이가 **구조적으로 불가능**해진다 —
# ADR-0036 이 검색 설명 LLM 을 아예 없앤 것과 같은 처방이다.
_FORECAST_D = """다음은 아이템 시세 예측 결과입니다. 사용자에게 2~3문장으로 설명하세요.

- 아래 결과는 내부 데이터입니다. **필드 이름(cold_start, baseline_price 등)을
  답변에 쓰지 마세요.** 사용자는 그 구조를 모릅니다.
- **결과에 있는 아이템 이름을 답변에 그대로 밝히세요.** 그 아이템이 이 예측의
  대상입니다.
- 기준가는 {baseline_source}입니다. 판매자가 정한 등록가와 혼동하지 마세요.
- {conditional}
- 완결된 문장으로 끝내세요.

결과: {result}"""

_ANOMALY_D = """다음은 거래 이상 여부 판정 결과입니다. 2~3문장으로 설명하세요.
가장 큰 기여 요인을 근거로 들어 설명하세요.

- 아래 결과는 내부 데이터입니다. **필드 이름(contributions, is_anomaly 등)을
  답변에 쓰지 마세요.**
- **마지막에 다음 내용을 완결된 한 문장으로 덧붙이세요**: 이 판정은 합성 데모
  거래 데이터를 대상으로 한 것이며, 사용자의 실제 거래 번호와는 체계가 다릅니다.

결과: {result}"""


# --- 에이전트(복합 분기) ----------------------------------------------------
#
# 복합 질의는 MCP 도구 결과 JSON 을 그대로 모델에게 보여준 뒤 최종 답변을 쓰게
# 한다. 그래서 **누출 경로가 하나 더 있다 — 도구 출력의 필드 이름**이다.
# 실제로 `cold_start: false` 가 `Cold Start 상태가 아닙니다` 로 나갔다.
#
# 여기서는 에이전트 전체를 돌리지 않는다(질의당 LLM 3회). 대신 **문제가 나는
# 그 지점만** 재현한다 — 시스템 프롬프트 + 도구 결과 페이로드 → 최종 문장.
# 도구 선택이나 루프는 이 결함과 무관하다.

_AGENT_SYSTEM_A = """당신은 게임 아이템 거래소의 상담 도우미입니다.

규칙:
- **등록가(listing_price)와 예측 기준가(baseline_price)는 기준이 다릅니다.**
  등락은 expected_change_pct를 쓰고, 기준가를 언급할 때는 baseline_source를
  같이 밝히세요.
- 시세 예측이 Cold Start(유사 아이템 추세 상속)로 나왔다면 그 점을 밝히세요.
- 답변은 한국어로, 근거를 같이 적으세요."""

_AGENT_SYSTEM_B = """당신은 게임 아이템 거래소의 상담 도우미입니다.

규칙:
- **등록가(listing_price)와 예측 기준가(baseline_price)는 기준이 다릅니다.**
  등락은 expected_change_pct를 쓰고, 기준가를 언급할 때는 baseline_source를
  같이 밝히세요.
- **도구 결과의 필드 이름을 답변에 쓰지 마세요.** 사용자는 그 구조를 모릅니다.
- 예측 결과에 estimate_note 가 있으면 그 내용을 반드시 답변에 반영하세요.
- 답변은 한국어로, 근거를 같이 적으세요."""


def agent_payload(result: dict[str, Any], variant: str) -> dict[str, Any]:
    """MCP `forecast_item_price` 가 내는 모양. A 는 옛 판, B 는 새 판."""
    base = {
        "item_id": result["item_id"],
        "name": result["name"],
        "baseline_price": result["anchor_price"],
        "expected_change_pct": result["expected_change_pct"],
        "history_days": result["history_days"],
    }
    if variant == "A(현재)":
        # 원시 불리언 — 이게 문장으로 새던 경로다.
        return {**base, "cold_start": result["cold_start"]}
    if result["cold_start"]:
        base["estimate_note"] = (
            "이 예측은 거래 이력이 부족해 비슷한 아이템들의 추세를 빌려 추정한 값입니다."
        )
    return base


AGENT_VARIANTS = ("A(현재)", "B(명시)")

VARIANTS = ("A(현재)", "B(명시)", "C(미언급)", "D(질의없음)")


def build_prompt(kind: str, variant: str, query: str, result: dict[str, Any]) -> str:
    if variant == "D(질의없음)":
        # **`query` 를 넘길 자리가 없다.** 다른 안들과 달리 시그니처가 다르므로
        # 먼저 가른다 — `.format(query=...)` 를 부르면 조용히 무시되는 게 아니라
        # 이 안의 요점이 사라진다.
        if kind == "forecast":
            cold = result["cold_start"]
            return _FORECAST_D.format(
                result=result,
                baseline_source=(
                    "최근 체결가" if not cold else "거래 이력 부족 상태의 추정 기준가"
                ),
                conditional=(
                    "이 아이템은 거래 이력이 부족해 비슷한 아이템들의 추세를 빌려 "
                    "추정한 값입니다. 그 점을 반드시 밝히세요."
                    if cold
                    else "이 아이템은 거래 이력이 충분합니다. 추정이라는 언급은 하지 마세요."
                ),
            )
        return _ANOMALY_D.format(result=result)

    if kind == "forecast":
        cold = result["cold_start"]
        baseline = "최근 체결가" if not cold else "거래 이력 부족 상태의 추정 기준가"
        # **조건 분기를 프롬프트가 아니라 코드가 한다.** A 는 모델에게
        # "cold_start 가 true 면" 을 읽히는데, 그러려면 모델이 그 필드를 봐야 하고
        # 그게 문장에 새어나온 경로다. 코드가 미리 갈라주면 모델은 필드를 볼
        # 이유가 없다.
        conditional = (
            "이 아이템은 거래 이력이 부족해 비슷한 아이템들의 추세를 빌려 추정한 "
            "값입니다. 그 점을 반드시 밝히세요."
            if cold
            else "이 아이템은 거래 이력이 충분합니다. 추정이라는 언급은 하지 마세요."
        )
        template = {"A(현재)": _FORECAST_A, "B(명시)": _FORECAST_B, "C(미언급)": _FORECAST_C}[variant]
        if variant == "A(현재)":
            return template.format(query=query, result=result, baseline_source=baseline)
        return template.format(
            query=query, result=result, baseline_source=baseline, conditional=conditional
        )

    template = {"A(현재)": _ANOMALY_A, "B(명시)": _ANOMALY_B, "C(미언급)": _ANOMALY_C}[variant]
    return template.format(query=query, result=result)


# --- 실행 ------------------------------------------------------------------
#
# **수집(collect)과 채점(score)이 분리돼 있다.** 인자 없이 돌리면 둘 다 하고,
# `--score-only` 는 저장된 답변을 다시 채점만 한다. 지표를 고칠 때 API 를 다시
# 태우지 않기 위해서다 — 실제로 모순 지표가 틀린 걸 발견하고 그 대가를 치렀다.


async def collect() -> list[dict[str, Any]]:
    settings = get_settings()
    if not settings.openai_api_key:
        raise SystemExit("OPENAI_API_KEY 가 필요합니다 (ai/.env)")

    es = AsyncElasticsearch(settings.elasticsearch_url)
    # 앱과 **같은 설정**으로 만든다. temperature 를 여기서 따로 정하면 측정이
    # 운영과 다른 조건에서 이뤄진다 (ADR-0017 이 그 함정을 기록했다).
    llm = OpenAIClient(
        api_key=settings.openai_api_key,
        model=settings.openai_model,
        temperature=settings.openai_temperature,
    )

    # (kind, query, result, 답변에 나오면 안 되는 주어)
    cases: list[tuple[str, str, dict[str, Any], str]] = []
    forecasts: dict[int, dict[str, Any]] = {}
    print("[준비] 도구 결과를 케이스당 한 번만 계산한다 - 네 안이 같은 입력을 본다")
    for item_id in {*FORECAST_ITEMS, *(i for i, _, _ in OOD_FORECAST_CASES)}:
        try:
            forecasts[item_id] = await forecast_price(
                es=es, tenant_code=TENANT, item_id=item_id
            )
        except Exception as error:
            print(f"  시세 item={item_id} 건너뜀: {type(error).__name__}")

    for item_id in FORECAST_ITEMS:
        if item_id in forecasts:
            result = forecasts[item_id]
            cases.append(("forecast", f"아이템 {item_id}번 시세 알려줘", result, ""))
            print(f"  시세 item={item_id}  cold_start={result['cold_start']}")

    # **도메인 밖 질의 × 진짜 결과.** 예측은 위와 같은 것을 재사용한다 — 다시
    # 계산하면 예측 자체의 변동이 프롬프트 차이로 잡힌다.
    for item_id, query, subject in OOD_FORECAST_CASES:
        if item_id in forecasts:
            cases.append(("forecast", query, forecasts[item_id], subject))
            print(f"  도메인밖 item={item_id}  주어={subject}  \"{query}\"")

    for trade_id in ANOMALY_TRADES:
        try:
            result = detect_trade(TENANT, trade_id, IdSpace.SYNTHETIC)
        except Exception as error:
            print(f"  이상 trade={trade_id} 건너뜀: {type(error).__name__}")
            continue
        cases.append(("anomaly", f"거래 {trade_id}번 이상거래야?", result, ""))
        print(f"  이상 trade={trade_id}  is_anomaly={result['is_anomaly']}")

    if not cases:
        await es.close()
        raise SystemExit("케이스가 없습니다 — 모델과 ES 색인을 먼저 준비하세요")

    total = len(cases) * len(VARIANTS) * REPEATS
    print(f"\n[수집] 케이스 {len(cases)} × 안 {len(VARIANTS)} × 반복 {REPEATS} = LLM {total}회")

    rows: list[dict[str, Any]] = []
    for kind, query, result, subject in cases:
        for variant in VARIANTS:
            prompt = build_prompt(kind, variant, query, result)
            for _ in range(REPEATS):
                answer = (await llm.complete(prompt)).strip()
                rows.append(
                    {
                        "variant": variant,
                        "kind": kind,
                        "query": query,
                        # 채점에 필요한 사실만 남긴다. 결과 전체를 넣으면 파일이
                        # 커지고, 정작 채점은 이것들만 본다.
                        "is_anomaly": bool(result.get("is_anomaly", False)),
                        "cold_start": bool(result.get("cold_start", False)),
                        "ood_subject": subject,
                        "item_name": str(result.get("name", "")),
                        "answer": answer,
                    }
                )
            print(f"  {kind:<9}{variant:<12}{query}")

    # --- 에이전트 경로 -----------------------------------------------------
    #
    # 시세 케이스만 쓴다 — 이 결함(도구 필드명 누출)이 나는 곳이 거기다.
    print()
    print("[수집] 에이전트 경로 (시스템 프롬프트 + 도구 페이로드 → 최종 문장)")
    for kind, query, result, subject in cases:
        # 도메인 밖 케이스는 여기서 제외한다 — 에이전트 경로의 방어는 MCP 도구가
        # 내는 안내문(ADR-0039)이지 이 최종 문장 프롬프트가 아니다. 섞으면 이
        # 표의 `주어채택` 열이 다른 방어선을 재게 된다.
        if kind != "forecast" or subject:
            continue
        for variant in AGENT_VARIANTS:
            system = _AGENT_SYSTEM_A if variant == "A(현재)" else _AGENT_SYSTEM_B
            payload = agent_payload(result, variant)
            prompt = (
                f"{system}"
                f"{NL}{NL}사용자 질의: {query}"
                f"{NL}forecast_item_price 결과: {payload}"
                f"{NL}{NL}최종 답변을 작성하세요."
            )
            for _ in range(REPEATS):
                answer = (await llm.complete(prompt)).strip()
                rows.append(
                    {
                        "variant": variant,
                        "kind": "agent",
                        "query": query,
                        "is_anomaly": False,
                        "cold_start": bool(result.get("cold_start", False)),
                        "ood_subject": "",
                        "item_name": str(result.get("name", "")),
                        "answer": answer,
                    }
                )
        print(f"  agent    {query}")

    await es.close()
    ANSWERS.parent.mkdir(parents=True, exist_ok=True)
    io.open(ANSWERS, "w", encoding="utf-8").write(
        json.dumps(rows, ensure_ascii=False, indent=1)
    )
    print(f"\n  답변 {len(rows)}건 저장 → {ANSWERS.relative_to(ANSWERS.parents[2])}")
    return rows


def score(rows: list[dict[str, Any]]) -> None:
    tally: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    flagged: dict[str, list[dict[str, Any]]] = defaultdict(list)
    samples: dict[str, str] = {}

    for row in rows:
        variant, kind, answer = row["variant"], row["kind"], row["answer"]
        result = {"is_anomaly": row["is_anomaly"], "cold_start": row["cold_start"]}
        tally[variant]["n"] += 1
        subject = row.get("ood_subject", "")
        if subject:
            tally[variant]["n밖"] += 1
        for label, hit in (
            ("누출", bool(leaked(answer))),
            ("미완결", incomplete(answer)),
            ("면책누락", missing_disclaimer(answer, kind, result)),
            ("모순", contradicts(answer, kind, result)),
            ("권유", advises(answer)),
            ("주어채택", adopts_subject(answer, subject)),
            ("대상명누락", omits_item_name(answer, row.get("item_name", ""))),
        ):
            if hit:
                tally[variant][label] += 1
                flagged[label].append(row)
        samples.setdefault(f"{variant}|{kind}", answer)

    print("\n" + "=" * 78)
    print("결과 - 낮을수록 좋다 (n = 답변 수)")
    print("=" * 78)
    print(f"  {'안':<14}{'n':>4}{'누출':>7}{'미완결':>8}{'면책누락':>9}{'모순':>7}"
          f"{'권유':>7}{'주어채택':>10}{'대상명누락':>12}")
    for variant in VARIANTS:
        row = tally[variant]
        # 파이썬 3.11 은 f-string 안에서 같은 따옴표를 다시 못 쓴다 — 미리 만든다.
        adopted = "{}/{}".format(row["주어채택"], row["n밖"])
        print(
            f"  {variant:<14}{row['n']:>4}{row['누출']:>7}{row['미완결']:>8}"
            f"{row['면책누락']:>9}{row['모순']:>7}{row['권유']:>7}"
            f"{adopted:>10}{row['대상명누락']:>12}"
        )
    print("\n  · 주어채택의 분모는 **도메인 밖 케이스만**이다 - 나머지 열과 다르다.")
    print("  · 주어채택은 게이트가 뚫렸다고 가정했을 때의 2차 방어선을 잰다.")

    # **걸린 것을 반드시 보여준다.** 지표가 스스로 틀릴 수 있다 — 실제로 첫
    # 판본의 모순 지표는 부정문을 못 읽어 세 안 모두 9를 냈고, 사람이 답변을
    # 봤기 때문에 드러났다. 숫자만 내는 검사는 자기 오류를 숨긴다.
    print("\n" + "=" * 78)
    print("걸린 답변 - 지표가 맞게 잡았는지 눈으로 확인할 것")
    print("=" * 78)
    for label in ("누출", "미완결", "면책누락", "모순", "권유", "주어채택", "대상명누락"):
        hits = flagged[label]
        print(f"\n  [{label}] {len(hits)}건")
        for row in hits[:3]:
            mark = f"is_anomaly={row['is_anomaly']}" if row["kind"] == "anomaly" else f"cold_start={row['cold_start']}"
            print(f"    · {row['variant']} / {row['kind']} / {mark}")
            if row.get("ood_subject"):
                print(f"      질의 \"{row['query']}\" / 실제 대상 {row['item_name']}")
            print(f"      {row['answer'][:170]}")
        if len(hits) > 3:
            print(f"    … 외 {len(hits) - 3}건")

    print("\n" + "=" * 78)
    print("표본 (안 × 분기마다 첫 답변)")
    print("=" * 78)
    for key in sorted(samples):
        print(f"\n  [{key}]\n  {samples[key]}")

    print("\n" + "=" * 78)
    print("판정 기준")
    print("=" * 78)
    print("  · 누출·미완결이 0에 가까우면서 **면책누락·모순이 A보다 나빠지지")
    print("    않아야** 채택한다. 하나를 고치며 다른 하나를 잃으면 안 된다.")
    print("  · B가 C보다 나쁘면 '쓰지 말라고 이름을 부르면 오히려 쓴다'는")
    print("    전례(97.5% → 22%)가 재현된 것이다.")
    print("  · **세 안이 같은 값을 낸 열은 프롬프트가 아니라 지표를 재고 있다.**")


async def main() -> None:
    if "--score-only" in sys.argv:
        if not ANSWERS.exists():
            raise SystemExit(f"저장된 답변이 없습니다: {ANSWERS}")
        rows = json.loads(io.open(ANSWERS, encoding="utf-8").read())
        print(f"[채점만] 저장된 답변 {len(rows)}건 - API 호출 없음")
        score(rows)
        return
    score(await collect())


if __name__ == "__main__":
    asyncio.run(main())
