"""도메인 게이트 — 밖을 잡는가, 안을 막지 않는가.

실행:
  python -m scripts.evaluate_domain_gate              # 수집 + 채점
  python -m scripts.evaluate_domain_gate --score-only # 저장된 답변 재채점 (무료)

## 고치려는 것

`"삼성전자 주식 어때?"` 가 시세 예측 분기를 그대로 타고 **"삼성전자 주식의
최근 거래가는 약 26,090원"** 이라고 답했다. 숫자는 `게임 머니 1000만 골드` 의
진짜 예측값이었고 주어만 거짓이었다. 라우터의 출력 공간에 "내 도메인이 아니다"
가 없어서, 어떤 질의든 5개 의도 중 하나로 강제 배정된다.

## 이 스크립트는 두 번 다른 것을 쟀다

처음에는 **`understand_query` 의 JSON 스키마에 `in_domain` 필드를 더하는** 안을
쟀다. LLM 호출이 늘지 않는 게 장점이었는데, 두 번 측정하고 두 번 다 기각됐다.

| | 1회차 | 2회차(문구 수정) | 기준 |
|---|---|---|---|
| 미검출 | 0/38 | 0/38 | <= 10% |
| 오거부(509건) | 29.9% | **13.4%** | <= 1% |
| 재작성 토큰집합 일치 (대조군 대비) | **-0.234** | **-0.248** | 0 이상 |

**재작성 손실이 문구를 고쳐도 그대로였다** — 즉 문구가 아니라 스키마가 늘어난
것 자체의 대가다. 그래서 판정을 별도 호출로 떼어냈고(`domain_gate.py`),
지금 이 스크립트가 재는 것은 그 전용 프롬프트다.

**필터 회귀 항목이 사라진 것은 기준을 지운 게 아니다.** 추출 프롬프트를 아예
건드리지 않으므로 구조적으로 0이고, 그 사실은 `tests/test_domain_gate.py` 의
`TestExtractionPromptStaysClean` 이 고정한다. 회귀를 "측정으로 통과"에서
"구조로 불가능"으로 옮긴 것이다.

## 두 방향을 같이 잰다 — 하나만 재면 만점이 쉽다

| 지표 | 안 재면 생기는 일 |
|---|---|
| **미검출** (밖을 통과시킴) | 고치려던 결함이 그대로 남는다 |
| **오거부** (안을 거절함) | "전부 거절" 이 미검출 0% 로 만점을 받는다 |
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
from app.corpus.out_of_domain import LOOKS_OUT_BUT_IN, OUT_OF_DOMAIN
from app.services.llm.openai_client import OpenAIClient
from app.services.search.domain_gate import _PROMPT, _parse

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DATA = Path(__file__).resolve().parents[1] / "data"
ANSWERS = DATA / "domain_gate_answers.json"

# 판정 프롬프트는 질의이해(약 1,400 토큰)의 1/6 수준이라 TPM 여유가 크다.
# 그래도 무한정 올리지는 않는다 — 429 는 재시도로 흡수하되 애초에 안 내는 게 낫다.
CONCURRENCY = 8
MAX_ATTEMPTS = 8
BACKOFF_CAP_SECONDS = 20

# 사전 등록 기준 — **결과를 보고 고쳐 쓰지 않는다.**
MAX_MISS_RATE = 0.10  # 도메인 밖을 통과시키는 비율
MAX_FALSE_REJECT_LABELED = 0  # 라벨 검색 질의는 한 건도 막히면 안 된다
MAX_FALSE_REJECT_RATE = 0.01  # 도메인 안 전체

# 이 비율을 넘게 실패하면 **채점하지 않는다.**
#
# 첫 실행이 52% 실패했는데도 판정을 냈다. 실패를 `None` 으로 두고 미검출을
# `is not False` 로 세는 바람에 **실패가 전부 미검출로 집계됐고**(63.2%), 오거부는
# `is False` 라 실패를 세지 않아 **실제보다 낮게** 나왔다. 같은 실패가 한 지표는
# 나쁘게, 다른 지표는 좋게 만들었다. 실패는 답이 아니라 결측이다.
MAX_ERROR_RATE = 0.02


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
    llm: OpenAIClient, semaphore: asyncio.Semaphore, query: str
) -> dict[str, Any]:
    """한 질의를 판정한다. **원문을 같이 남긴다** — 파싱이 틀릴 수도 있다."""
    async with semaphore:
        last = ""
        for attempt in range(MAX_ATTEMPTS):
            try:
                raw = await llm.complete(_PROMPT.format(query=query))
                return {"in_domain": _parse(raw), "raw": raw.strip(), "error": None}
            except Exception as error:  # noqa: BLE001 — 실패도 결과다
                last = f"{type(error).__name__}: {error}"
                if "429" not in last and "rate_limit" not in last:
                    break
                await asyncio.sleep(min(2**attempt, BACKOFF_CAP_SECONDS))
        return {"in_domain": None, "raw": "", "error": last}


async def collect() -> dict[str, Any]:
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
    labeled = sum(1 for _, source in in_domain if source == "labeled")

    print(f"[수집] 도메인 안 {len(in_domain)}건 (라벨 {labeled} + 발화 "
          f"{len(in_domain) - labeled})")
    print(f"       도메인 밖 {len(ood)}건, 경계(안인데 밖처럼) {len(boundary)}건")
    print(f"       => LLM {len(in_domain) + len(ood) + len(boundary)}회, "
          f"동시 {CONCURRENCY}\n")

    async def run(queries: list[str], label: str) -> list[dict[str, Any]]:
        rows = await asyncio.gather(*(_one(llm, semaphore, q) for q in queries))
        merged = [{**row, "query": query} for query, row in zip(queries, rows)]
        print(f"  {label:<22}{len(merged):>5}건  실패 "
              f"{sum(1 for r in merged if r['error'])}")
        return merged

    payload = {
        "in_domain": await run([q for q, _ in in_domain], "도메인 안"),
        "sources": {query: source for query, source in in_domain},
        "ood": await run(ood, "도메인 밖"),
        "boundary": await run(boundary, "경계"),
        "ood_groups": {query: group for query, group in OUT_OF_DOMAIN},
        "boundary_groups": {query: group for query, group in LOOKS_OUT_BUT_IN},
    }

    ANSWERS.parent.mkdir(parents=True, exist_ok=True)
    io.open(ANSWERS, "w", encoding="utf-8").write(
        json.dumps(payload, ensure_ascii=False, indent=1)
    )
    print(f"\n  저장 → {ANSWERS.name}")
    return payload


def score(payload: dict[str, Any]) -> None:
    sources = payload["sources"]
    ood_groups = payload["ood_groups"]

    # --- 0. 실패를 먼저 본다 -----------------------------------------------
    everything = [
        row for key in ("in_domain", "ood", "boundary") for row in payload[key]
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

    def answered(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [row for row in rows if row["error"] is None]

    ood_ok = answered(payload["ood"])
    missed = [row for row in ood_ok if row["in_domain"] is True]
    miss_rate = len(missed) / len(ood_ok)

    in_ok = answered(payload["in_domain"])
    rejected = [row for row in in_ok if row["in_domain"] is False]
    rejected_labeled = [r for r in rejected if sources.get(r["query"]) == "labeled"]
    labeled_ok = sum(1 for row in in_ok if sources.get(row["query"]) == "labeled")
    reject_rate = len(rejected) / len(in_ok)
    boundary_ok = answered(payload["boundary"])
    boundary_rejected = [row for row in boundary_ok if row["in_domain"] is False]

    print("\n" + "=" * 78)
    print("1. 미검출 — 도메인 밖을 통과시킨 비율 (낮을수록 좋다)")
    print("=" * 78)
    print(f"  {len(missed)}/{len(ood_ok)} = {miss_rate:.1%}"
          f"   기준 <= {MAX_MISS_RATE:.0%}"
          f"   {'충족' if miss_rate <= MAX_MISS_RATE else '미충족'}")
    if missed:
        print(f"\n  통과시킨 부류: "
              f"{dict(Counter(ood_groups.get(r['query'], '?') for r in missed))}")
        for row in missed:
            print(f"    [{ood_groups.get(row['query'], '?')}] {row['query']}")
            print(f"           모델 출력: {row['raw'][:80]}")

    print("\n" + "=" * 78)
    print("2. 오거부 — 도메인 안을 거절한 비율 (낮을수록 좋다)")
    print("=" * 78)
    print(f"  전체    {len(rejected)}/{len(in_ok)} = {reject_rate:.2%}"
          f"   기준 <= {MAX_FALSE_REJECT_RATE:.0%}"
          f"   {'충족' if reject_rate <= MAX_FALSE_REJECT_RATE else '미충족'}")
    print(f"  라벨    {len(rejected_labeled)}/{labeled_ok}"
          f"   기준 = {MAX_FALSE_REJECT_LABELED}건"
          f"   {'충족' if len(rejected_labeled) <= MAX_FALSE_REJECT_LABELED else '미충족'}")
    print(f"  경계    {len(boundary_rejected)}/{len(boundary_ok)}"
          f"   (기준 없음 — 일부러 어렵게 고른 표본이라 참고용)")
    # **걸린 것을 반드시 보여준다.** 숫자만 내는 검사는 자기 오류를 숨긴다.
    for row in rejected[:15]:
        print(f"    [{sources.get(row['query'], '?')}] {row['query']}")
    if len(rejected) > 15:
        print(f"    … 외 {len(rejected) - 15}건")
    for row in boundary_rejected:
        print(f"    [경계/{payload['boundary_groups'].get(row['query'], '?')}] "
              f"{row['query']}")

    bars = [
        ("미검출", miss_rate <= MAX_MISS_RATE),
        ("오거부(라벨)", len(rejected_labeled) <= MAX_FALSE_REJECT_LABELED),
        ("오거부(전체)", reject_rate <= MAX_FALSE_REJECT_RATE),
    ]
    print("\n" + "=" * 78)
    print("사전 등록 기준 — 결과를 보고 고쳐 쓰지 않는다")
    print("=" * 78)
    for name, ok in bars:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    print("  (필터 회귀 항목은 없어진 게 아니라 **구조적으로 0**이다 — 추출")
    print("   프롬프트를 건드리지 않는다. test_domain_gate.py 가 고정한다)")
    print(f"\n  => {'채택 가능' if all(ok for _, ok in bars) else '기준 미달 — 기준이 아니라 프롬프트를 고칠 것'}")


async def main() -> None:
    if "--score-only" in sys.argv:
        if not ANSWERS.exists():
            raise SystemExit(f"저장된 답변이 없습니다: {ANSWERS}")
        payload = json.loads(io.open(ANSWERS, encoding="utf-8").read())
        print("[채점만] 저장된 결과 재채점 — API 호출 없음")
        score(payload)
        return
    score(await collect())


if __name__ == "__main__":
    asyncio.run(main())
