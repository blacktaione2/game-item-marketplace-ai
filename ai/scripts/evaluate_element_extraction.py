"""속성(element) 추출 정확도 — 그리고 결정적 후처리가 얼마나 건지는가.

실행:
  python -m scripts.evaluate_element_extraction --tag run1        # 수집 + 채점
  python -m scripts.evaluate_element_extraction --tag run1 --score-only

## 고치려는 것

`"무속성 검 찾아줘"` 가 `element="무속성"` 을 **50번 중 8번만** 낸다. 나머지는
`null` 이고, 그러면 속성 필터가 아예 안 걸려 불꽃의 대검이 섞여 나온다. 코퍼스
42건 중 **35건이 무속성**이라, 이 필터가 빠지면 검색이 사실상 무필터가 된다.

## 프롬프트로 안 고친다 — **이미 시켰는데 안 된다**

`query_understanding.py` 의 프롬프트에는 이미 이렇게 적혀 있다.

    - 무속성/속성 없는 -> "무속성"
    - null("속성 조건 없음")과 "무속성"("속성이 없는 아이템만")은 다릅니다.

**정확히 옳은 말로 이미 지시받았고 그런데도 42/50 을 틀린다.** 그리고 이 자리는
이 저장소가 프롬프트 한 줄로 **97.5% → 22%** 를 겪은 바로 그 자리다 — 혼동 대상을
이름으로 부르면 모델이 그쪽으로 쏠렸다. 도메인 게이트 라운드에서도 프롬프트 변형
셋이 연달아 기각됐다.

그래서 **결정 단계가 보장할 수 있는 것을 LLM 에게 다시 묻지 않는다**(ADR-0036·0039와
같은 처방). 질의에 글자 그대로 `무속성` 이 있는데 추출이 `null` 을 냈으면 코드가
채운다.

## A/B 에 추가 호출이 들지 않는다

후처리는 **질의와 추출 결과만으로 결정되는 함수**다. 그래서 원본 추출을 한 번
수집해두면 "후처리 적용" 은 채점 시점에 계산할 수 있다 — `--score-only` 로
전후를 몇 번이든 다시 볼 수 있고 API 호출은 0이다.

이 저장소가 설명 프롬프트 라운드에서 배운 것과 같다: **수집과 채점을 분리하면
지표를 하나 더 넣는 데 드는 비용이 0이 된다.**

## 반복 실행이 필요하다

이 결함은 **확률적**이다(8/50). 한 번 돌려 맞았다고 고쳐진 게 아니다. 질의마다
여러 번 돌려 비율로 본다.
"""

from __future__ import annotations

import asyncio
import io
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.corpus.element_queries import ELEMENT_GROUPS, ELEMENT_QUERIES
from app.services.llm.openai_client import OpenAIClient
from app.services.search.query_understanding import _PROMPT, _parse

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DATA = Path(__file__).resolve().parents[1] / "data"

REPEATS = 3
CONCURRENCY = 6
MAX_ATTEMPTS = 6
BACKOFF_CAP_SECONDS = 20

# 실패가 이 비율을 넘으면 **채점하지 않는다.** 실패는 답이 아니라 결측이다 —
# 도메인 게이트 1차에서 443건 실패를 "미검출" 로 세는 바람에 63.2% 라는 가짜
# 수치가 나왔다(사례 19).
MAX_ERROR_RATE = 0.02

# 사전 등록 기준 — **결과를 보고 고쳐 쓰지 않는다.**
#
# 후처리가 있어야 통과하는 값으로 잡는다. 지금 기준선은 무속성 무리가 거의 0 이다.
MIN_MUSOKSEONG_RATE = 0.95  # `무속성` 무리에서 "무속성" 을 내는 비율
MAX_MISEONGEUP_FALSE = 0  # **미언급 무리에 무속성이 채워지면 한 건도 안 된다**
MAX_JEOHANG_FALSE = 0  # 저항 무리도 마찬가지
MAX_BUJEONG_FALSE = 0  # 부정형에 무속성을 채우면 정반대를 준다
MIN_TASOKSEONG_RATE = 0.95  # 다른 속성이 후처리 때문에 나빠지면 안 된다


def _fill_from_query(query: str, element: str | None) -> str | None:
    """**채점 쪽 사본이 아니라 구현을 그대로 부른다.**

    후처리를 여기에 다시 적으면 둘이 갈라지고, 갈라진 채로도 숫자는 멀쩡히
    나온다 — 이 저장소가 검사 사례집에 반복해 적은 종류의 침묵이다.
    """
    from app.services.search.query_understanding import fill_missing_element

    return fill_missing_element(query, element)


async def _one(
    llm: OpenAIClient, semaphore: asyncio.Semaphore, query: str
) -> dict[str, Any]:
    async with semaphore:
        last = ""
        for attempt in range(MAX_ATTEMPTS):
            try:
                raw = await llm.complete(_PROMPT.format(query=query))
                parsed = _parse(raw)
                return {
                    "element": parsed.filters.element,
                    "rewritten": parsed.rewritten_query,
                    "error": None,
                }
            except Exception as error:  # noqa: BLE001 — 실패도 결과다
                last = f"{type(error).__name__}: {error}"
                if "429" not in last and "rate_limit" not in last:
                    break
                await asyncio.sleep(min(2**attempt, BACKOFF_CAP_SECONDS))
        return {"element": None, "rewritten": "", "error": last}


async def collect(tag: str) -> dict[str, Any]:
    settings = get_settings()
    if not settings.openai_api_key:
        raise SystemExit("OPENAI_API_KEY 가 필요합니다 (ai/.env)")

    llm = OpenAIClient(
        api_key=settings.openai_api_key,
        model=settings.openai_model,
        temperature=settings.openai_temperature,
    )
    semaphore = asyncio.Semaphore(CONCURRENCY)

    total = len(ELEMENT_QUERIES) * REPEATS
    print(f"[수집] 질의 {len(ELEMENT_QUERIES)}건 × {REPEATS}회 = **LLM {total}회**")
    print("       이 결함은 확률적(8/50)이라 한 번으로는 판단할 수 없다\n")

    rows: list[dict[str, Any]] = []
    for attempt in range(REPEATS):
        results = await asyncio.gather(
            *(_one(llm, semaphore, q) for q, _, _ in ELEMENT_QUERIES)
        )
        for (query, answer, group), got in zip(ELEMENT_QUERIES, results):
            rows.append({**got, "query": query, "expected": answer, "group": group,
                         "run": attempt})
        failed = sum(1 for r in results if r["error"])
        print(f"  {attempt + 1}회차  {len(results)}건  실패 {failed}")

    payload = {"tag": tag, "repeats": REPEATS, "rows": rows}
    path = _answers_path(tag)
    path.parent.mkdir(parents=True, exist_ok=True)
    io.open(path, "w", encoding="utf-8").write(
        json.dumps(payload, ensure_ascii=False, indent=1)
    )
    print(f"\n  저장 → {path.name}")
    return payload


def _answers_path(tag: str) -> Path:
    return DATA / f"element_extraction_answers_{tag}.json"


def score(payload: dict[str, Any]) -> None:
    rows = payload["rows"]

    errors = [r for r in rows if r["error"]]
    rate = len(errors) / len(rows)
    print(f"\n[수집 품질] 실패 {len(errors)}/{len(rows)} = {rate:.2%}"
          f"   허용 <= {MAX_ERROR_RATE:.0%}")
    if rate > MAX_ERROR_RATE:
        print(f"  대표 오류: {errors[0]['error'][:160]}")
        raise SystemExit("\n실패가 너무 많아 채점하지 않습니다. 다시 수집하세요.")

    ok_rows = [r for r in rows if r["error"] is None]

    # **같은 수집으로 전후를 둘 다 낸다.** 후처리는 결정적 함수라 추가 호출이 없다.
    for row in ok_rows:
        row["after"] = _fill_from_query(row["query"], row["element"])

    def summarize(key: str) -> dict[str, dict[str, int]]:
        stats: dict[str, dict[str, int]] = defaultdict(
            lambda: {"correct": 0, "total": 0, "무속성오채움": 0}
        )
        for row in ok_rows:
            got, want, group = row[key], row["expected"], row["group"]
            stats[group]["total"] += 1
            if got == want:
                stats[group]["correct"] += 1
            if got == "무속성" and want != "무속성":
                stats[group]["무속성오채움"] += 1
        return stats

    before, after = summarize("element"), summarize("after")

    print("\n" + "=" * 78)
    print(f"무리별 정확도  (tag={payload.get('tag', '?')}, {payload['repeats']}회 반복)")
    print("=" * 78)
    print("  {:<10}{:>16}{:>16}{:>14}".format("무리", "후처리 전", "후처리 후", "무속성 오채움"))
    for group in ELEMENT_GROUPS:
        b, a = before[group], after[group]
        if not b["total"]:
            continue
        print("  {:<10}{:>16}{:>16}{:>14}".format(
            group,
            "{}/{} ({:.0%})".format(b["correct"], b["total"], b["correct"] / b["total"]),
            "{}/{} ({:.0%})".format(a["correct"], a["total"], a["correct"] / a["total"]),
            "{} -> {}".format(b["무속성오채움"], a["무속성오채움"]),
        ))

    # --- 바뀐 것을 전부 보여준다 -------------------------------------------
    changed = [r for r in ok_rows if r["element"] != r["after"]]
    print(f"\n후처리가 손댄 판정: {len(changed)}/{len(ok_rows)}")
    seen: set[tuple[str, Any, Any]] = set()
    for row in changed:
        key = (row["query"], row["element"], row["after"])
        if key in seen:
            continue
        seen.add(key)
        mark = "OK " if row["after"] == row["expected"] else "**틀림**"
        print(f"  {mark} [{row['group']}] {row['query']}"
              f"   {row['element']} -> {row['after']}  (정답 {row['expected']})")

    # --- 남은 오답 ----------------------------------------------------------
    wrong = [r for r in ok_rows if r["after"] != r["expected"]]
    print(f"\n후처리 뒤에도 틀린 것: {len(wrong)}/{len(ok_rows)}")
    counted: dict[tuple[str, Any], int] = defaultdict(int)
    for row in wrong:
        counted[(row["query"], row["after"])] += 1
    for (query, got), n in sorted(counted.items(), key=lambda kv: -kv[1]):
        want = next(w for q, w, _ in ELEMENT_QUERIES if q == query)
        print(f"  {n}회  {query}   냄={got}  정답={want}")

    # --- 사전 등록 기준 ------------------------------------------------------
    def rate_of(stats: dict[str, dict[str, int]], group: str) -> float:
        s = stats[group]
        return s["correct"] / s["total"] if s["total"] else 1.0

    bars = [
        (f"무속성 정확도 >= {MIN_MUSOKSEONG_RATE:.0%}",
         rate_of(after, "무속성") >= MIN_MUSOKSEONG_RATE),
        (f"타속성 정확도 >= {MIN_TASOKSEONG_RATE:.0%} (회귀)",
         rate_of(after, "타속성") >= MIN_TASOKSEONG_RATE),
        ("미언급에 무속성 오채움 = 0",
         after["미언급"]["무속성오채움"] <= MAX_MISEONGEUP_FALSE),
        ("저항에 무속성 오채움 = 0",
         after["저항"]["무속성오채움"] <= MAX_JEOHANG_FALSE),
        ("부정형에 무속성 오채움 = 0",
         after["부정형"]["무속성오채움"] <= MAX_BUJEONG_FALSE),
    ]
    print("\n" + "=" * 78)
    print("사전 등록 기준 — 결과를 보고 고쳐 쓰지 않는다")
    print("=" * 78)
    for label, ok in bars:
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    print(f"\n  => {'채택 가능' if all(ok for _, ok in bars) else '기준 미달'}")
    print("  (미언급·저항·부정형의 오채움 기준이 '전부 무속성으로 채우기' 를 막는다)")


def _arg(flag: str, default: str) -> str:
    if flag in sys.argv:
        return sys.argv[sys.argv.index(flag) + 1]
    return default


async def main() -> None:
    tag = _arg("--tag", "run1")
    if "--score-only" in sys.argv:
        path = _answers_path(tag)
        if not path.exists():
            raise SystemExit(f"저장된 답변이 없습니다: {path}")
        print(f"[채점만] {path.name} 재채점 — API 호출 없음")
        score(json.loads(io.open(path, encoding="utf-8").read()))
        return
    score(await collect(tag))


if __name__ == "__main__":
    asyncio.run(main())
