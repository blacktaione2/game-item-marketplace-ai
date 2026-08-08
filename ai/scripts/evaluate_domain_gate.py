"""도메인 게이트 — 밖을 잡는가, 안을 막지 않는가.

실행:
  python -m scripts.evaluate_domain_gate --tag run1        # 수집 + 채점
  python -m scripts.evaluate_domain_gate --tag run1 --score-only   # 재채점 (무료)
  python -m scripts.evaluate_domain_gate --tag run2 --variants control,B

## 고치려는 것

`"삼성전자 주식 어때?"` 가 시세 예측 분기를 그대로 타고 **"삼성전자 주식의
최근 거래가는 약 26,090원"** 이라고 답했다. 숫자는 `게임 머니 1000만 골드` 의
진짜 예측값이었고 주어만 거짓이었다. 라우터의 출력 공간에 "내 도메인이 아니다"
가 없어서, 어떤 질의든 5개 의도 중 하나로 강제 배정된다.

## 이 스크립트는 세 번 다른 것을 쟀다

1. **`understand_query` 의 JSON 스키마에 `in_domain` 필드를 더하는** 안. 두 번
   측정하고 두 번 다 기각됐다 — 재작성 토큰집합 일치도가 대조군 대비
   **-0.234 → -0.248**, **문구를 고쳐도 그대로**였다. 즉 문구가 아니라 스키마가
   늘어난 것 자체의 대가다.
2. **별도 호출로 떼어낸 판정 프롬프트**(`domain_gate.py`). 채택됐다.
3. **지금**: 그 프롬프트의 오거부 잔여를 고치는 변형 비교.

## 3차 라운드가 고치는 것 — 진단이 바뀌었다

ADR-0039 는 잔여 오거부를 *"막연함과 주제 이탈이 한 불리언에 눌려 있다"* 로
적었다. **거절된 7건을 전수로 읽으니 그 설명이 2건을 설명하지 못한다.**

| 질의 | 대상이 있나 |
|---|---|
| `장신구 시장이 어떻게 될지 모르겠어.` | **있다 (장신구)** |
| `물약 시장은 어떻게 보고 있어?` | **있다 (물약)** |
| `돈 되는 거 뭐 있어` · `3만원 이하로 살 만한 거` | 없다 |
| `그냥 지나치기 아깝네` 외 2건 | 없다 |

앞의 둘은 막연하지 않다 — 취급 품목을 이름으로 부르고 시세를 묻는다. 세 번째
값(VAGUE)을 만들어도 구제되지 않는다.

일곱 건을 **전부** 설명하는 성질은 다른 것이었다: 모델이 *"주제가 안인가"* 가
아니라 **"내가 답해줄 수 있는가"** 를 판정하고 있다. 그리고 현행 프롬프트는
`"이 거래소에서 다룰 수 있는 이야기면 YES"` 라고 **실제로 그렇게 묻고 있다.**

그래서 바꾸는 것은 출력 공간이 아니라 **질문**이다.

| 변형 | 무엇이 다른가 |
|---|---|
| `control` | 현행 (배포본) |
| `A` | 한 줄 추가 — "요청을 들어줄 수 있는지는 묻지 않았다" |
| `B` | 판정 대상을 *요청* 에서 *대상* 으로 재구성. **A 를 포함한다** |

**B 가 A 를 포함하므로 셋을 같이 돌리면 ablation 이 된다** — A 만으로 고쳐지면
진단이 맞은 것이고, B 에서만 고쳐지면 원인이 문장 구조에 있다. "한 번에 하나씩"
을 순차 실행이 아니라 동시 대조로 만족시킨다.

## 두 방향을 같이 잰다 — 하나만 재면 만점이 쉽다

| 지표 | 안 재면 생기는 일 |
|---|---|
| **미검출** (밖을 통과시킴) | 고치려던 결함이 그대로 남는다 |
| **오거부** (안을 거절함) | "전부 거절" 이 미검출 0% 로 만점을 받는다 |

## 표본이 작다는 것을 숫자로 같이 낸다

경계 세트는 v1 이 **16건**이었다. 거기서 2건과 1건의 차이는 **최소 측정 단위
하나**이고, 효과가 없어도 우연히 나올 수 있다. 그래서 이 스크립트는 경계 거절에
**Clopper-Pearson 95% 구간**을 같이 찍는다 — 기준 통과/실패만 내면 잡음을
성과로 읽게 된다.

같은 이유로 경계 세트를 16 → 32 로 늘렸고, **늘리면 예전 수치와 분모가 달라지므로
v1 부분집합도 같이 보고한다**(`BOUNDARY_V1`).
"""

from __future__ import annotations

import asyncio
import io
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.corpus.out_of_domain import (
    BOUNDARY_V1,
    HOLDOUT_IN,
    HOLDOUT_OUT,
    HOLDOUT_QUERIES,
    LOOKS_OUT_BUT_IN,
    OOD_V1_EXCLUDED,
    OUT_OF_DOMAIN,
)
from app.services.llm.openai_client import OpenAIClient
from app.services.search.domain_gate import _PROMPT, _parse

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DATA = Path(__file__).resolve().parents[1] / "data"

# 판정 프롬프트는 질의이해(약 1,400 토큰)의 1/6 수준이라 TPM 여유가 크다.
# 그래도 무한정 올리지는 않는다 — 429 는 재시도로 흡수하되 애초에 안 내는 게 낫다.
CONCURRENCY = 8
MAX_ATTEMPTS = 8
BACKOFF_CAP_SECONDS = 20

# 사전 등록 기준 — **결과를 보고 고쳐 쓰지 않는다.**
MAX_MISS_RATE = 0.10  # 도메인 밖을 통과시키는 비율
MAX_FALSE_REJECT_LABELED = 0  # 라벨 검색 질의는 한 건도 막히면 안 된다
MAX_FALSE_REJECT_RATE = 0.01  # 도메인 안 전체
MAX_BOUNDARY_REJECTED = 1  # 경계 32건. **표본이 작다 — 구간을 같이 본다**

# 이 비율을 넘게 실패하면 **채점하지 않는다.**
#
# 첫 실행이 52% 실패했는데도 판정을 냈다. 실패를 `None` 으로 두고 미검출을
# `is not False` 로 세는 바람에 **실패가 전부 미검출로 집계됐고**(63.2%), 오거부는
# `is False` 라 실패를 세지 않아 **실제보다 낮게** 나왔다. 같은 실패가 한 지표는
# 나쁘게, 다른 지표는 좋게 만들었다. 실패는 답이 아니라 결측이다.
MAX_ERROR_RATE = 0.02

# gpt-4o-mini, 2026-08 기준 ($/1M 토큰). 비용을 어림이라도 남기는 이유는 이
# 프로젝트가 다른 라운드(일일 한도 최악 케이스 등)에서 계속 해온 일과 맞추기
# 위해서다 — 측정 자체의 값을 모르면 "더 재자" 를 판단할 수 없다.
PRICE_INPUT_PER_1M = 0.150
PRICE_OUTPUT_PER_1M = 0.600


# --- 변형 -------------------------------------------------------------------
#
# **변형은 여기 살고 `domain_gate.py` 에는 안 산다.** 후보는 코드가 아니라 측정
# 대상이다. 채택된 것만 그쪽으로 옮긴다.

# A: 현행에 **한 줄만** 더한다. 진단을 그대로 겨냥한 최소 변경이다.
_PROMPT_A = _PROMPT.replace(
    "다른 말은 쓰지 마세요.\n",
    "다른 말은 쓰지 마세요.\n\n"
    "- 판단 기준은 **무엇에 대한 이야기인가** 하나입니다. 그 요청을 들어줄 수\n"
    "  있는지, 답할 정보가 있는지는 묻지 않았습니다.\n",
    1,
)

# B: 판정 대상을 *요청* 에서 *대상* 으로 옮긴다. **A 의 한 줄을 포함한다.**
#
# 마지막 줄이 열거가 아니라 **기준**인 것이 핵심이다. 취급 품목을 나열했다가
# 오거부가 4.13% → 5.30% 로 오른 전례가 있다(ADR-0039) — 열거는 포함을 넓히려고
# 넣어도 배제의 근거로 쓰인다.
_PROMPT_B = """다음은 사용자가 게임 아이템 거래소에 입력한 문장입니다.
이 거래소는 게임 아이템·게임 계정·게임 재화(골드)를 사고파는 곳입니다.

문장이 **말하는 대상**이 이 거래소에서 사고팔 수 있는 것이면 YES, 아니면 NO 만
출력하세요. 다른 말은 쓰지 마세요.

- 판단 기준은 **무엇에 대한 이야기인가** 하나입니다. 그 요청을 들어줄 수
  있는지, 답할 정보가 있는지는 묻지 않았습니다.
- 게임 안의 물건(장비·아이템·계정·게임 재화)이 대상이면 YES입니다. 찾기, 값
  묻기, 오를지 묻기, 살지 말지 고민, 성능 비교, 거래 점검이 다 포함됩니다.
- **이름·은어·줄임말·오타만 있어도 YES**입니다. 무슨 뜻인지 몰라도 게임 안의
  물건 이름처럼 보이면 YES입니다(예: "9강 대검", "70방투구", "빙결 스태프").
- 대상이 흐릿하거나 아예 없어도 YES입니다(예: "이거 괜찮아?", "그냥 봐줘",
  "ㄱㅊ?"). 무엇을 가리키는지는 나중에 물어보면 됩니다.
- **사고팔 수 있는 물건이 아닌 것**이 대상일 때만 NO입니다.

문장: {query}"""

# C: 막연함 절을 **값·예산형까지** 넓힌다. 다른 줄은 건드리지 않는다.
#
# 1차에서 배포본이 자른 9건 중 4건이 한 부류였다 — `3만원 이하로 살 만한 거`,
# `10만원 넘는 건 안 볼래`, `싸게 나온 거 위주로`, `돈 되는 거 뭐 있어`. 프롬프트에
# 이미 "막연해도 YES" 가 있는데 **예시가 전부 지시대명사**(`이거 괜찮아?`,
# `그냥 봐줘`)여서, 모델이 그 절을 대명사형에만 적용했다.
#
# **예시는 tune·held-out 어디에도 없는 것으로 지었다**(말뭉치 1,659문장 대조).
# 걸린 표본을 그대로 넣으면 그 질의는 다음 측정에서 자동으로 맞고, 오거부율이
# 실력이 아니라 암기가 된다.
_PROMPT_C = _PROMPT.replace(
    '- 무엇을 가리키는지 막연해도(예: "이거 괜찮아?", "그냥 봐줘") YES입니다.',
    '- 무엇을 가리키는지 막연해도 YES입니다(예: "이거 괜찮아?", "그냥 봐줘").\n'
    "  **값·예산만 말하고 물건 이름을 안 대도 마찬가지입니다**"
    '(예: "3천원까지만", "주머니 사정이 빠듯해").',
    1,
)

VARIANTS: dict[str, str] = {
    "control": _PROMPT,
    "A": _PROMPT_A,
    "B": _PROMPT_B,
    "C": _PROMPT_C,
}


def _assert_a_is_a_real_edit() -> None:
    """`str.replace` 가 아무것도 못 바꿔도 **조용히 원본을 돌려준다.**

    그러면 A 가 대조군과 글자까지 같아지고, 두 열이 나란히 같은 숫자를 낸다 —
    "차이가 없다" 로 읽히지만 사실은 **변형을 재지 않은 것**이다. 이 저장소가
    출력 경로를 입력으로 쓴 설정값에서 겪은 것과 같은 종류의 침묵이다.
    """
    if _PROMPT_A == _PROMPT:
        raise SystemExit("변형 A 가 원본과 같습니다 — replace 대상 문구를 확인하세요")
    if "묻지 않았습니다" not in _PROMPT_B:
        raise SystemExit("변형 B 에 A 의 한 줄이 없습니다 — ablation 이 성립하지 않습니다")
    if _PROMPT_C == _PROMPT:
        raise SystemExit("변형 C 가 원본과 같습니다 — replace 대상 문구를 확인하세요")


def _assert_holdout_is_clean() -> None:
    """**프롬프트의 예시가 held-out 에 있으면 held-out 이 아니다.**

    그 질의는 다음 측정에서 자동으로 맞으므로, 일반화가 아니라 암기를 재게 된다.
    문자열 하나 겹치는 것만으로 무너지는데 숫자는 멀쩡히 나오는 종류라 고정한다.
    """
    for name, prompt in VARIANTS.items():
        for query in HOLDOUT_QUERIES:
            if query in prompt:
                raise SystemExit(f"변형 {name} 프롬프트에 held-out 질의가 있습니다: {query}")


_assert_a_is_a_real_edit()
_assert_holdout_is_clean()


def load_in_domain() -> list[tuple[str, str]]:
    """`understand_query` 를 실제로 타는 도메인 안 질의 전부.

    `faq_smalltalk` 과 `anomaly_check` 발화는 뺀다 — 그 분기는 검색을 타지
    않으므로 여기서 거절돼도 화면에 영향이 없다. 넣으면 오거부율의 분모만
    키워서 지표를 실제보다 좋아 보이게 만든다.
    """
    rows: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(query: str, source: str) -> None:
        query = query.strip()
        if query and query not in seen:
            seen.add(query)
            rows.append((query, source))

    for name in ("eval_queries.jsonl", "eval_queries_manual.jsonl"):
        for line in io.open(DATA / name, encoding="utf-8"):
            add(json.loads(line)["query"], "labeled")
    for line in io.open(DATA / "train_triplets.jsonl", encoding="utf-8"):
        add(json.loads(line)["anchor"], "labeled")

    reaches_gate = {"item_search", "price_forecast", "compound"}
    for entry in json.loads(io.open(DATA / "intent_train.json", encoding="utf-8").read()):
        if entry["intent"] in reaches_gate:
            add(entry["text"], "utterance")

    return rows


async def _one(
    llm: OpenAIClient, semaphore: asyncio.Semaphore, prompt: str, query: str
) -> dict[str, Any]:
    """한 질의를 판정한다. **원문을 같이 남긴다** — 파싱이 틀릴 수도 있다."""
    async with semaphore:
        last = ""
        for attempt in range(MAX_ATTEMPTS):
            try:
                raw = await llm.complete(prompt.format(query=query))
                return {"in_domain": _parse(raw), "raw": raw.strip(), "error": None}
            except Exception as error:  # noqa: BLE001 — 실패도 결과다
                last = f"{type(error).__name__}: {error}"
                if "429" not in last and "rate_limit" not in last:
                    break
                await asyncio.sleep(min(2**attempt, BACKOFF_CAP_SECONDS))
        return {"in_domain": None, "raw": "", "error": last}


def _estimate_cost(prompts: list[str], calls_per_prompt: int) -> tuple[int, float]:
    """대략 토큰 수와 달러. **한글 1자 = 1토큰으로 잡아 과대평가한다.**

    o200k_base 는 한글을 그보다 잘 묶으므로 실제는 이보다 싸다. 비용 진술은
    안전한 방향으로 틀리는 게 낫다 — `tiktoken` 을 새 의존성으로 들이지 않는
    이유이기도 하다(이 값의 용도는 "재도 되는가" 판단 하나다).
    """
    per_call = sum(len(p) for p in prompts) / len(prompts) + 20  # 질의 길이 여유
    total_in = int(per_call * calls_per_prompt * len(prompts))
    total_out = calls_per_prompt * len(prompts)  # YES/NO 한 토큰
    dollars = (
        total_in / 1_000_000 * PRICE_INPUT_PER_1M
        + total_out / 1_000_000 * PRICE_OUTPUT_PER_1M
    )
    return total_in + total_out, dollars


async def collect(tag: str, names: list[str]) -> dict[str, Any]:
    settings = get_settings()
    if not settings.openai_api_key:
        raise SystemExit("OPENAI_API_KEY 가 필요합니다 (ai/.env)")

    llm = OpenAIClient(
        api_key=settings.openai_api_key,
        model=settings.openai_model,
        # 앱과 같은 설정으로 잰다. 여기서 따로 정하면 운영과 다른 조건이 된다.
        temperature=settings.openai_temperature,
    )
    semaphore = asyncio.Semaphore(CONCURRENCY)

    in_domain = load_in_domain()
    ood = [q for q, _ in OUT_OF_DOMAIN]
    boundary = [q for q, _ in LOOKS_OUT_BUT_IN]
    hold_in = [q for q, _ in HOLDOUT_IN]
    hold_out = [q for q, _ in HOLDOUT_OUT]
    labeled = sum(1 for _, source in in_domain if source == "labeled")
    per_variant = len(in_domain) + len(ood) + len(boundary) + len(HOLDOUT_QUERIES)

    tokens, dollars = _estimate_cost([VARIANTS[n] for n in names], per_variant)
    print(f"[수집] 도메인 안 {len(in_domain)}건 (라벨 {labeled} + 발화 "
          f"{len(in_domain) - labeled})")
    print(f"       도메인 밖 {len(ood)}건 (v1 {len(ood) - len(OOD_V1_EXCLUDED)}), "
          f"경계 {len(boundary)}건 (v1 {len(BOUNDARY_V1)})")
    print(f"       held-out {len(hold_in)}+{len(hold_out)}건 - **채택 판정 전용, "
          f"한 번만 쓴다**")
    print(f"       변형 {names} × {per_variant}건 = **LLM {per_variant * len(names)}회**, "
          f"동시 {CONCURRENCY}")
    print(f"       어림 비용: ~{tokens:,} 토큰, ~${dollars:.2f} "
          f"({settings.openai_model}, 한글 1자=1토큰으로 과대평가)\n")

    async def run(prompt: str, queries: list[str], label: str) -> list[dict[str, Any]]:
        rows = await asyncio.gather(
            *(_one(llm, semaphore, prompt, q) for q in queries)
        )
        merged = [{**row, "query": query} for query, row in zip(queries, rows)]
        print(f"  {label:<26}{len(merged):>5}건  실패 "
              f"{sum(1 for r in merged if r['error'])}")
        return merged

    runs: dict[str, Any] = {}
    for name in names:
        prompt = VARIANTS[name]
        print(f"  [{name}]")
        runs[name] = {
            "in_domain": await run(prompt, [q for q, _ in in_domain], "도메인 안"),
            "ood": await run(prompt, ood, "도메인 밖"),
            "boundary": await run(prompt, boundary, "경계"),
            "holdout_in": await run(prompt, hold_in, "held-out 안"),
            "holdout_out": await run(prompt, hold_out, "held-out 밖"),
        }

    payload = {
        "tag": tag,
        "runs": runs,
        "sources": {query: source for query, source in in_domain},
        "ood_groups": {query: group for query, group in OUT_OF_DOMAIN},
        "boundary_groups": {query: group for query, group in LOOKS_OUT_BUT_IN},
        "holdout_groups": {q: g for q, g in HOLDOUT_IN + HOLDOUT_OUT},
    }

    path = _answers_path(tag)
    path.parent.mkdir(parents=True, exist_ok=True)
    io.open(path, "w", encoding="utf-8").write(
        json.dumps(payload, ensure_ascii=False, indent=1)
    )
    print(f"\n  저장 → {path.name}")
    return payload


def _answers_path(tag: str) -> Path:
    """**실행마다 다른 파일에 쓴다.**

    예전 판본은 한 파일을 덮어썼다. 그러면 대조군을 새로 뜬 순간 비교 상대가
    사라진다 — 재채점(`--score-only`)이 공짜라는 이점도 마지막 실행에만 남는다.
    """
    return DATA / f"domain_gate_answers_{tag}.json"


def _clopper_pearson(successes: int, total: int) -> tuple[float, float]:
    """정확 이항 95% 구간. 근사 없이 베타 분위수로 낸다.

    **표본이 16~32건이라 정규 근사가 못 쓴다.** 그리고 구간을 안 내면 2건과
    1건의 차이가 성과로 읽힌다 — 실제로는 최소 측정 단위 하나다.
    """
    def beta_ppf(p: float, a: float, b: float) -> float:
        # 이분법. scipy 없이 충분한 정밀도(1e-6)를 얻는다.
        if a <= 0:
            return 0.0
        low, high = 0.0, 1.0
        for _ in range(60):
            mid = (low + high) / 2
            if _beta_cdf(mid, a, b) < p:
                low = mid
            else:
                high = mid
        return (low + high) / 2

    if total == 0:
        return 0.0, 1.0
    lower = 0.0 if successes == 0 else beta_ppf(0.025, successes, total - successes + 1)
    upper = 1.0 if successes == total else beta_ppf(0.975, successes + 1, total - successes)
    return lower, upper


def _beta_cdf(x: float, a: float, b: float) -> float:
    """정규화 불완전 베타. 연분수 전개(Lentz)."""
    import math

    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    log_beta = math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)
    front = math.exp(math.log(x) * a + math.log(1 - x) * b - log_beta) / a
    if x >= (a + 1) / (a + b + 2):
        return 1.0 - _beta_cdf(1 - x, b, a)

    f, c, d = 1.0, 1.0, 0.0
    for i in range(300):
        m = i // 2
        if i == 0:
            numerator = 1.0
        elif i % 2 == 0:
            numerator = (m * (b - m) * x) / ((a + 2 * m - 1) * (a + 2 * m))
        else:
            numerator = -((a + m) * (a + b + m) * x) / ((a + 2 * m) * (a + 2 * m + 1))
        d = 1.0 + numerator * d
        d = 1e-30 if abs(d) < 1e-30 else d
        d = 1.0 / d
        c = 1.0 + numerator / c
        c = 1e-30 if abs(c) < 1e-30 else c
        f *= c * d
        if abs(1.0 - c * d) < 1e-10:
            break
    return front * (f - 1.0)


def _branch_of(query: str) -> tuple[str, str]:
    """이 질의가 **어느 분기로 가는가** — 미검출 옆에 같이 찍는다.

    ## 왜 필요한가

    이 스크립트는 **게이트만** 잰다. 그런데 게이트는 `item_search` 와
    `price_forecast` 에만 걸려 있다 — `faq_smalltalk` 로 가는 질의는 게이트를
    아예 지나지 않고, `_DEFAULT_FAQ` 가 범위를 밝히며 거절한다(LLM 0회).

    그래서 **`미검출 N/M` 은 게이트 성적이지 파이프라인 성적이 아니다.** 배포본
    실측(2026-08-07)에서 사각지대 8건 중 4건이 FAQ 로 가 이미 올바르게 거절됐다.
    분기를 안 찍으면 이 수치가 "사용자에게 그대로 새어나간 비율" 로 읽힌다.

    ## 그런데 분모에서 빼지는 않는다

    처음엔 "게이트에 안 닿는 건 빼자" 고 했는데 **틀렸다.** 같은 8건을 로컬과
    배포본에서 라우팅해보니 **6/8 만 일치했고, 어긋난 2건은 둘 다 분류기 판정**
    이다(룰 판정은 정규식이라 완전히 재현됐다). `세트 효과 조건이 뭐야` 는 로컬
    `compound`, 배포본 `price_forecast` 였다.

    **"게이트에 닿는가" 는 질의의 안정된 속성이 아니다.** 학습마다 가중치가 달라
    오늘 FAQ 로 가는 질의가 내일 시세로 간다. 그래서 이 값은 분모를 고치는 근거가
    아니라 **읽는 사람에게 주는 단서**다.

    같은 이유로 ADR-0039 의 커버리지 논증(`search+forecast 22 + agent 7 + FAQ 9
    = 38/38`)도 **질의별이 아니라 분기별로 읽어야 한다** — 어느 분기로 가든
    게이트·안내문·FAQ 중 하나가 받는다는 것이 실제 보장이고, 특정 질의가 특정
    분기로 간다는 것은 보장이 아니다.
    """
    from app.services.router.router import route

    decision = route(query)
    return decision["intent"].value, decision["decided_by"]


def _answered(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if row["error"] is None]


def _measure(run: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    sources = payload["sources"]

    ood_ok = _answered(run["ood"])
    missed = [r for r in ood_ok if r["in_domain"] is True]
    ood_v1 = [r for r in ood_ok if r["query"] not in OOD_V1_EXCLUDED]
    missed_v1 = [r for r in ood_v1 if r["in_domain"] is True]

    in_ok = _answered(run["in_domain"])
    rejected = [r for r in in_ok if r["in_domain"] is False]
    rejected_labeled = [r for r in rejected if sources.get(r["query"]) == "labeled"]
    labeled_ok = sum(1 for r in in_ok if sources.get(r["query"]) == "labeled")

    boundary_ok = _answered(run["boundary"])
    boundary_rejected = [r for r in boundary_ok if r["in_domain"] is False]
    boundary_v1 = [r for r in boundary_ok if r["query"] in BOUNDARY_V1]
    boundary_v1_rejected = [r for r in boundary_v1 if r["in_domain"] is False]

    # **run1 에는 held-out 이 없다.** 없는 실행을 재채점할 때 터지지 않게 한다 —
    # 다만 빈 값을 0 으로 세면 "held-out 무결점"으로 읽히므로, 아래 판정에서
    # 세트가 없으면 기준 자체를 내지 않는다.
    hold_in_ok = _answered(run.get("holdout_in", []))
    hold_out_ok = _answered(run.get("holdout_out", []))
    hold_rejected = [r for r in hold_in_ok if r["in_domain"] is False]
    hold_missed = [r for r in hold_out_ok if r["in_domain"] is True]
    groups = dict(HOLDOUT_IN)
    hold_budget_rejected = [
        r for r in hold_rejected if groups.get(r["query"]) == "예산형"
    ]

    return {
        "has_holdout": bool(hold_in_ok or hold_out_ok),
        "hold_rejected": hold_rejected,
        "hold_in_n": len(hold_in_ok),
        "hold_missed": hold_missed,
        "hold_out_n": len(hold_out_ok),
        "hold_budget_rejected": hold_budget_rejected,
        "missed": missed,
        "miss_rate": len(missed) / len(ood_ok) if ood_ok else 0.0,
        "ood_n": len(ood_ok),
        "missed_v1": len(missed_v1),
        "ood_v1_n": len(ood_v1),
        "rejected": rejected,
        "reject_rate": len(rejected) / len(in_ok) if in_ok else 0.0,
        "in_n": len(in_ok),
        "rejected_labeled": rejected_labeled,
        "labeled_n": labeled_ok,
        "boundary_rejected": boundary_rejected,
        "boundary_n": len(boundary_ok),
        "boundary_v1_rejected": len(boundary_v1_rejected),
        "boundary_v1_n": len(boundary_v1),
    }


def score(payload: dict[str, Any]) -> None:
    ood_groups = payload["ood_groups"]
    boundary_groups = payload["boundary_groups"]
    sources = payload["sources"]

    # --- 0. 실패를 먼저 본다 -----------------------------------------------
    everything = [
        row
        for run in payload["runs"].values()
        for key in ("in_domain", "ood", "boundary", "holdout_in", "holdout_out")
        for row in run.get(key, [])
    ]
    errors = [row for row in everything if row["error"]]
    error_rate = len(errors) / len(everything)
    print(f"\n[수집 품질] 실패 {len(errors)}/{len(everything)} = {error_rate:.2%}"
          f"   허용 <= {MAX_ERROR_RATE:.0%}")
    if error_rate > MAX_ERROR_RATE:
        print(f"  대표 오류: {errors[0]['error'][:160]}")
        raise SystemExit(
            "\n실패가 너무 많아 채점하지 않습니다. 실패를 어느 한쪽 답으로 세면\n"
            "그 지표는 게이트가 아니라 API 를 재게 됩니다. 다시 수집하세요."
        )

    stats = {name: _measure(run, payload) for name, run in payload["runs"].items()}

    # --- 1. 나란히 ---------------------------------------------------------
    print("\n" + "=" * 78)
    print(f"변형 비교  (tag={payload.get('tag', '?')})")
    print("=" * 78)
    print("  {:<10}{:>12}{:>16}{:>14}{:>10}".format(
        "", "미검출", "오거부(전체)", "오거부(라벨)", "경계"))
    for name, s in stats.items():
        print("  {:<10}{:>12}{:>16}{:>14}{:>10}".format(
            name,
            "{}/{}".format(len(s["missed"]), s["ood_n"]),
            "{}/{} ({:.2%})".format(len(s["rejected"]), s["in_n"], s["reject_rate"]),
            "{}/{}".format(len(s["rejected_labeled"]), s["labeled_n"]),
            "{}/{}".format(len(s["boundary_rejected"]), s["boundary_n"]),
        ))
    print("\n  v1 분모(ADR-0039 비교용):")
    for name, s in stats.items():
        print(f"    {name:<10} 미검출 {s['missed_v1']}/{s['ood_v1_n']}"
              f"   경계 {s['boundary_v1_rejected']}/{s['boundary_v1_n']}")

    # --- 2. 표본이 작다는 것을 숫자로 --------------------------------------
    print("\n" + "=" * 78)
    print("경계 거절 - 95% 구간 (Clopper-Pearson). **한 건 차이의 무게를 본다**")
    print("=" * 78)
    for name, s in stats.items():
        low, high = _clopper_pearson(len(s["boundary_rejected"]), s["boundary_n"])
        rate = len(s["boundary_rejected"]) / s["boundary_n"] if s["boundary_n"] else 0
        print(f"  {name:<10}{len(s['boundary_rejected'])}/{s['boundary_n']} = "
              f"{rate:.1%}   95% CI [{low:.1%}, {high:.1%}]")
    print("\n  구간이 겹치면 '개선' 이라고 부르지 않는다 - 두 번째 실행을 본다.")

    # --- 2-b. held-out ------------------------------------------------------
    if any(s["has_holdout"] for s in stats.values()):
        print("\n" + "=" * 78)
        print("held-out - **한 번도 안 쓴 세트. 채택은 여기서 정한다**")
        print("=" * 78)
        for name, s in stats.items():
            if not s["has_holdout"]:
                continue
            print(f"  {name:<10}오거부 {len(s['hold_rejected'])}/{s['hold_in_n']}"
                  f"   미검출 {len(s['hold_missed'])}/{s['hold_out_n']}"
                  f"   (예산형 거절 {len(s['hold_budget_rejected'])})")
        holdout_groups = payload.get("holdout_groups", {})
        for name, s in stats.items():
            if not s["has_holdout"]:
                continue
            for row in s["hold_rejected"]:
                print(f"    [{name}] 거절 [{holdout_groups.get(row['query'], '?')}] "
                      f"{row['query']}")
            for row in s["hold_missed"]:
                print(f"    [{name}] 통과 [{holdout_groups.get(row['query'], '?')}] "
                      f"{row['query']}")
        # **held-out 이 현상을 재현했는가 — 기준을 읽기 전에 봐야 한다.**
        # 대조군이 예산형을 하나도 안 자르면 이 세트에는 잴 신호가 없다. 그때
        # "C 가 예산형을 0건 자름" 은 개선의 증거가 아니라 무정보다.
        control_stat = stats.get("control")
        if control_stat is not None and control_stat["has_holdout"]:
            reproduced = len(control_stat["hold_budget_rejected"])
            print(f"\n  [전제] 대조군이 held-out 예산형을 {reproduced}건 자름 - "
                  f"{'현상 재현됨, 일반화 기준 적용' if reproduced else '**재현 안 됨: 일반화는 확인 불가**'}")

    # --- 3. 변형별 상세 ----------------------------------------------------
    for name, s in stats.items():
        print("\n" + "=" * 78)
        print(f"[{name}] 걸린 것 전부")
        print("=" * 78)
        print("  * 미검출은 **게이트 성적이지 파이프라인 성적이 아니다** -")
        print("    faq_smalltalk 로 가는 질의는 게이트를 지나지 않고 _DEFAULT_FAQ 가")
        print("    이미 거절한다. 분기를 같이 찍되 분모에서 빼지는 않는다")
        print("    (라우팅은 분류기 판정일 때 실행마다 달라진다 - 실측 6/8 일치).")
        if s["missed"]:
            print(f"  미검출 부류: "
                  f"{dict(Counter(ood_groups.get(r['query'], '?') for r in s['missed']))}")
            for row in s["missed"]:
                branch, decided = _branch_of(row["query"])
                print(f"    통과 [{ood_groups.get(row['query'], '?')}] {row['query']}"
                      f"   -> {branch} ({decided})")
        for row in s["rejected"][:15]:
            print(f"    거절 [{sources.get(row['query'], '?')}] {row['query']}")
        if len(s["rejected"]) > 15:
            print(f"    … 외 {len(s['rejected']) - 15}건")
        for row in s["boundary_rejected"]:
            print(f"    거절 [경계/{boundary_groups.get(row['query'], '?')}] "
                  f"{row['query']}")

    # --- 4. 사전 등록 기준 --------------------------------------------------
    control = stats.get("control")
    print("\n" + "=" * 78)
    print("사전 등록 기준 - 결과를 보고 고쳐 쓰지 않는다")
    print("=" * 78)
    for name, s in stats.items():
        bars = [
            ("미검출 <= 10%", s["miss_rate"] <= MAX_MISS_RATE),
            ("오거부(라벨) = 0", len(s["rejected_labeled"]) <= MAX_FALSE_REJECT_LABELED),
            ("오거부(전체) <= 1%", s["reject_rate"] <= MAX_FALSE_REJECT_RATE),
            ("경계 거절 <= 1", len(s["boundary_rejected"]) <= MAX_BOUNDARY_REJECTED),
        ]
        # **대조군보다 미검출이 나빠지면 기각한다.** B 는 판정 기준을 좁히므로
        # 오거부를 줄이면서 미검출을 늘릴 수 있다 — 그 맞교환을 미리 막는다.
        if control is not None and name != "control":
            bars.append(
                ("미검출 <= 대조군", len(s["missed"]) <= len(control["missed"]))
            )
            # --- held-out 기준 ---------------------------------------------
            #
            # **전부 대조군 상대값이다.** 절대 기준을 걸면 1차에서 드러난
            # 사각지대(게임 메커니즘 질문)가 C 의 책임으로 잡히는데, 그건 C 가
            # 고치려는 것도 아니고 배포본에 이미 있던 결함이다. 같은 세트에서
            # 대조군도 재므로 상대값으로 보면 그 부분이 상쇄된다.
            #
            # **이 세트는 한 번만 쓴다.** 여기서 떨어지면 라운드는 변경 없이
            # 끝난다 — 다시 만지려면 새 held-out 을 만들어야 한다. 안 그러면
            # held-out 이 두 번째 tune 세트가 될 뿐이고, 그건 이 저장소가 리랭커
            # 문턱에서 이미 값을 치른 실수다(ADR-0018).
            if s["has_holdout"] and control["has_holdout"]:
                bars.append((
                    "held-out 오거부 <= 대조군",
                    len(s["hold_rejected"]) <= len(control["hold_rejected"]),
                ))
                bars.append((
                    "held-out 미검출 <= 대조군",
                    len(s["hold_missed"]) <= len(control["hold_missed"]),
                ))
                # 겨냥한 부류에서 **실제로 나아야** 한다. 이게 없으면 "아무것도
                # 안 바꾼 프롬프트"도 위 두 기준을 통과한다.
                #
                # 단 **대조군이 held-out 예산형을 하나도 안 자르면 이 기준은
                # 성립하지 않는다** — `< 0` 은 만족 불가능하고, 그건 C 의 실패가
                # 아니라 held-out 이 현상을 재현하지 못했다는 뜻이다. 그때는
                # 기준을 내지 않고 "일반화는 확인 못 했다"고 말한다.
                if control["hold_budget_rejected"]:
                    bars.append((
                        "held-out 예산형 < 대조군 (일반화)",
                        len(s["hold_budget_rejected"])
                        < len(control["hold_budget_rejected"]),
                    ))
        print(f"\n  [{name}]")
        for label, ok in bars:
            print(f"    {'PASS' if ok else 'FAIL'}  {label}")
        print(f"    => {'채택 가능' if all(ok for _, ok in bars) else '기준 미달'}")
    print("\n  (필터 회귀 항목은 없어진 게 아니라 **구조적으로 0**이다 - 추출")
    print("   프롬프트를 건드리지 않는다. test_domain_gate.py 가 고정한다)")


def _arg(flag: str, default: str) -> str:
    if flag in sys.argv:
        return sys.argv[sys.argv.index(flag) + 1]
    return default


async def main() -> None:
    tag = _arg("--tag", "run1")
    names = [n for n in _arg("--variants", "control,A,B").split(",") if n]
    unknown = [n for n in names if n not in VARIANTS]
    if unknown:
        raise SystemExit(f"모르는 변형: {unknown} (있는 것: {list(VARIANTS)})")

    if "--score-only" in sys.argv:
        path = _answers_path(tag)
        if not path.exists():
            raise SystemExit(f"저장된 답변이 없습니다: {path}")
        payload = json.loads(io.open(path, encoding="utf-8").read())
        print(f"[채점만] {path.name} 재채점 - API 호출 없음")
        score(payload)
        return
    score(await collect(tag, names))


if __name__ == "__main__":
    asyncio.run(main())
