"""하드 필터(subcategory / element)가 부적합 결과를 얼마나 걷어내는지 측정한다.

실행: python -m scripts.evaluate_hard_filters [--repeat N]

## 왜 리랭커 하한 스크립트와 따로 두는가

`evaluate_rerank_floor.py`는 "점수로 자를 수 있는가"라는 **기각된** 질문에
답하는 스크립트다(ADR-0014). 임계값을 훑어 추천값까지 내놓기 때문에 지금
그걸 재활용하면 기각 기록이 흐려진다. 여기서 재는 것은 다르다 —
**임계값이 없는 필터가 남긴 부적합 결과가 몇 건인가.**

## 라벨은 여전히 아이템 "이름" 기반이다 (중요)

`is_fit()`은 `element` 필드를 보지 않는다. 필드로 라벨하면 "element 필터가
element 조건을 만족시켰는가"가 되어 **순환논증**이다. 필터가 쓰는 신호와
라벨이 쓰는 신호를 분리해야 측정이 성립한다.

대가가 있다: 이름에 속성어가 없는 속성 아이템(`성기사의 워메이스`=신성,
`폭풍의 창`=번개, `용의 숨결 지팡이`=화염)은 속성 질의에서 필터가 남겨도
라벨은 부적합으로 센다. 현재 질의셋(화염 검 / 냉기 지팡이)에서는 이 셋이
필터를 통과할 수 없어 실제로 발생하지 않지만, 질의를 늘릴 때는 확인해야 한다.

## 0건 재현성

`expect_none_fit` 질의는 **정확히 0건**이 나와야 정상이다. 그런데 질의 재작성이
비결정적이라 실행마다 필터 추출이 흔들릴 수 있다 — `--repeat`으로 같은 질의를
여러 번 돌려 0건이 재현되는지 본다. 한 번 0건인 것은 증거가 아니다.

## 이전/이후를 같은 실행에서 잰다

`element` 필터를 끈 패스와 켠 패스를 연달아 돌린다. 지난 실행의 집계 수치와
비교하면 그 사이에 질의 재작성이 달라진 효과가 섞여 들어간다 — 재작성 비결정성
때문에 리랭커 하한이 기각됐던 것과 같은 함정이다(ADR-0014). 같은 실행 안에서
비교하면 최소한 같은 코드/같은 색인이라는 건 보장된다.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from contextlib import contextmanager

from app.corpus.rerank_floor_queries import FLOOR_QUERIES, FloorQuery, is_fit
from app.services.llm.dependencies import get_llm_client
from app.services.search.es_client import get_es_client
from app.services.search.filters import SearchFilters
from app.services.search.pipeline import search


@contextmanager
def element_filter_disabled():
    """`element` 절만 빼고 검색한다 — "이전" 상태 재현용.

    측정 스크립트 안에서만 쓰는 몽키패치다. 프로덕션 경로에 "필터 끄기" 플래그를
    만들 이유는 없다.
    """
    original = SearchFilters.to_es_filters

    def patched(self: SearchFilters):
        return original(self.model_copy(update={"element": None}))

    SearchFilters.to_es_filters = patched
    try:
        yield
    finally:
        SearchFilters.to_es_filters = original


async def run_query(es, query: FloorQuery, size: int) -> dict:
    result = await search(
        es=es,
        llm_client=get_llm_client(),
        tenant_code="nexon",
        query=query.query,
        size=size,
    )
    documents = result["results"]
    return {
        "query": query,
        "filters": result["filters"],
        "rewritten": result["rewritten_query"],
        "documents": documents,
        "fit": sum(1 for doc in documents if is_fit(doc["name"], query)),
        "unfit": sum(1 for doc in documents if not is_fit(doc["name"], query)),
    }


def print_run(rows: list[dict]) -> None:
    print(f"\n{'=' * 72}\n질의별 결과\n{'=' * 72}")
    for row in rows:
        query: FloorQuery = row["query"]
        tag = ""
        if query.expect_all_fit:
            tag = "  [통제군]"
        elif query.expect_none_fit:
            tag = "  [정답 0건]"
        held = "  (홀드아웃)" if query.holdout else ""
        print(f"\n  {query.query}{tag}{held}")
        print(f"    필터  {row['filters']}")
        print(f"    재작성 \"{row['rewritten']}\"")
        print(f"    결과 {len(row['documents'])}건 — 적합 {row['fit']} / 부적합 {row['unfit']}")

        if not row["documents"]:
            verdict = "정상 (조건 만족 아이템 없음)" if query.expect_none_fit else "**예상 못한 0건**"
            print(f"      => 0건. {verdict}")
        for doc in row["documents"]:
            mark = "적합  " if is_fit(doc["name"], query) else "부적합"
            print(
                f"      {mark} {doc['name']:<20} {doc.get('subcategory', '?'):<5}"
                f" {doc.get('element', '?'):<5} {doc['price']:>9,.0f}"
                f"  rerank {doc.get('rerank_score', 0.0):>6.2f}"
            )

    total_fit = sum(r["fit"] for r in rows)
    total_unfit = sum(r["unfit"] for r in rows)
    print(f"\n{'=' * 72}")
    print(f"합계  적합 {total_fit}건 / 부적합 {total_unfit}건 (수집 {total_fit + total_unfit}건)")

    empty = [r["query"].query for r in rows if not r["documents"]]
    print(f"0건 질의 {len(empty)}건: {empty}")

    for row in rows:
        if row["query"].expect_none_fit and row["documents"]:
            print(
                f"  ! 정답 0건 질의 `{row['query'].query}`가 "
                f"{len(row['documents'])}건을 돌려줬다 — 근사치가 나가고 있다"
            )


def print_comparison(before: list[dict], after: list[dict]) -> None:
    """이전(element 필터 없음) vs 이후.

    하드 필터의 **비용은 적합 결과 손실**이다. 부적합이 줄어도 적합이 같이
    줄면 개선이 아니다 — 질의별로 둘을 나란히 본다.
    """
    print(f"\n{'=' * 72}\nelement 필터 이전/이후\n{'=' * 72}")
    print(f"{'질의':<26}{'적합 이전→이후':>16}{'부적합 이전→이후':>18}")

    fit_losses = []
    for b, a in zip(before, after):
        query = a["query"].query
        loss = b["fit"] - a["fit"]
        flag = ""
        if loss > 0:
            flag = f"  <- 적합 {loss}건 손실"
            fit_losses.append((query, loss))
        elif b["unfit"] != a["unfit"]:
            flag = "  <-"
        print(
            f"{query:<26}{b['fit']:>7} → {a['fit']:<6}{b['unfit']:>9} → {a['unfit']:<6}{flag}"
        )

    b_fit, a_fit = sum(r["fit"] for r in before), sum(r["fit"] for r in after)
    b_unfit, a_unfit = sum(r["unfit"] for r in before), sum(r["unfit"] for r in after)
    print(f"\n{'합계':<26}{b_fit:>7} → {a_fit:<6}{b_unfit:>9} → {a_unfit:<6}")
    if b_unfit:
        print(f"  부적합 {b_unfit - a_unfit}건 제거 ({(b_unfit - a_unfit) / b_unfit:.0%})")
    if fit_losses:
        print(f"  ! **적합 손실 {sum(l for _, l in fit_losses)}건** — {fit_losses}")
    else:
        print("  적합 손실 0건")

    b_empty = {r["query"].query for r in before if not r["documents"]}
    a_empty = {r["query"].query for r in after if not r["documents"]}
    print(f"  0건이 된 질의: {sorted(a_empty - b_empty) or '없음'}")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--size", type=int, default=8)
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="정답 0건 질의를 몇 번 반복해 재현성을 볼지 (재작성 비결정성 때문)",
    )
    args = parser.parse_args()

    es = get_es_client()
    try:
        print(f"질의 {len(FLOOR_QUERIES)}건 × 상위 {args.size}건")

        print("\n[이전] element 필터 없음 (subcategory까지만)")
        with element_filter_disabled():
            before = [await run_query(es, q, args.size) for q in FLOOR_QUERIES]

        rows = [await run_query(es, query, args.size) for query in FLOOR_QUERIES]
        print_run(rows)
        print_comparison(before, rows)

        if args.repeat > 1:
            none_fit = [q for q in FLOOR_QUERIES if q.expect_none_fit]
            print(f"\n{'=' * 72}\n0건 재현성 — 정답 0건 질의 {len(none_fit)}건 × {args.repeat}회\n{'=' * 72}")
            for query in none_fit:
                counts: Counter[int] = Counter()
                filters_seen: set[str] = set()
                for _ in range(args.repeat):
                    row = await run_query(es, query, args.size)
                    counts[len(row["documents"])] += 1
                    filters_seen.add(str(row["filters"]))
                print(f"\n  {query.query}")
                print(f"    결과 건수 분포: {dict(sorted(counts.items()))}")
                zero = counts.get(0, 0)
                print(f"    0건 {zero}/{args.repeat}회" + ("  <- 재현됨" if zero == args.repeat else "  <- **흔들린다**"))
                if len(filters_seen) > 1:
                    print(f"    추출된 필터가 {len(filters_seen)}가지로 갈렸다:")
                    for seen in sorted(filters_seen):
                        print(f"      {seen}")
    finally:
        await es.close()


if __name__ == "__main__":
    asyncio.run(main())
