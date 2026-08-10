"""리랭커 점수 하한이 실제로 분리 가능한지 측정한다.

실행:
  python -m scripts.evaluate_rerank_floor --runs 5   # 재측정(기본)
  python -m scripts.evaluate_rerank_floor --runs 1   # 1회 수집(초기 형태)

## 이 스크립트의 이력

2026-07-30에 이걸로 하한을 **기각**했다. 이유는 입력 노이즈가 신호보다
컸다는 것 — 같은 절차를 두 번 돌렸는데 홀드아웃 판정이 뒤집혔다(ADR-0014).

2026-07-31에 그 노이즈의 원인이 잡혔다. `temperature`가 미설정이라 API
기본값 1.0으로 돌고 있었고, 0으로 두니 재작성 토큰집합 모드 일치율이
0.840 → 0.990이 됐다(ADR-0017). 그래서 **기각의 전제가 사라졌는지** 다시
재려고 반복 측정 기능을 붙였다.

## 반복 측정이 재는 것 — 셋은 다른 질문이다

| | 질문 | 왜 따로 봐야 하나 |
|---|---|---|
| **산포** | 점수가 실행마다 얼마나 흔들리는가 | 마진과 직접 비교할 수 있는 값 |
| **재현성** | **결론**이 실행마다 같은가 | 산포가 작아도 임계값이 경계에 붙어 있으면 결론은 뒤집힌다 |
| **순위 역전** | 부적합이 적합보다 위에 있는가 | 있으면 하한으로 **원리적으로** 못 푼다 |

산포가 마진보다 작아졌다고 하한이 성립하는 게 아니다. 세 개를 다 봐야 한다.

## 재는 것

`"5만원 이하 검"`에 활이 섞여 나오는 문제를 리랭커 점수로 자를 수 있는가.
리랭커는 이미 그걸 최하위로 판정하고 있으니(−4.15), 하한만 두면 될 것 같지만
**점수가 전부 음수여서 절대 임계값을 쓸 수 있는지가 불확실하다.**

두 방식을 같이 잰다.

- **절대 하한** `score >= T`
- **상대 격차** `score >= top1 - D` (1위 대비 낙폭)

각 방식에서 임계값을 훑으며 두 오류를 같이 본다.

| 오류 | 의미 | 비용 |
|---|---|---|
| 적합인데 잘림 | 맞는 결과를 잃음 | **높음** — 검색이 망가진다 |
| 부적합인데 남음 | 엉뚱한 것이 계속 노출 | 지금 상태와 같음 |

**적합을 자르지 않으면서 부적합을 얼마나 자를 수 있는가**가 판단 기준이다.
하나라도 적합을 자르는 임계값은 채택하지 않는다 — 지금 문제는 "쓸데없는 게
섞인다"이지 "결과가 안 나온다"가 아니라서, 후자를 만드는 건 개선이 아니다.
"""

from __future__ import annotations

import argparse
import asyncio
from itertools import combinations

import numpy as np

from app.corpus.rerank_floor_queries import FLOOR_QUERIES, FloorQuery, is_fit
from app.services.llm.dependencies import get_llm_client
from app.services.search.es_client import get_es_client
from app.services.search.pipeline import search

ABSOLUTE_FLOORS = [-6.0, -5.0, -4.5, -4.0, -3.5, -3.0, -2.5, -2.0, -1.0, 0.0]
RELATIVE_GAPS = [4.0, 3.0, 2.5, 2.0, 1.5, 1.0, 0.5]


async def collect(size: int) -> list[dict]:
    """모든 질의를 돌려 (질의, 아이템, 점수, 적합 여부)를 모은다."""
    es = get_es_client()
    rows: list[dict] = []
    try:
        for query in FLOOR_QUERIES:
            result = await search(
                es=es,
                llm_client=get_llm_client(),
                tenant_code="nexon",
                query=query.query,
                size=size,
            )
            documents = result["results"]
            if not documents:
                print(f"  ! 결과 0건: {query.query}")
                continue
            top1 = max(doc.get("rerank_score", 0.0) for doc in documents)
            for rank, doc in enumerate(documents, start=1):
                rows.append(
                    {
                        "query": query,
                        "name": doc["name"],
                        "rank": rank,
                        "score": float(doc.get("rerank_score", 0.0)),
                        "top1": top1,
                        "fit": is_fit(doc["name"], query),
                    }
                )
    finally:
        await es.close()
    return rows


def report_distribution(rows: list[dict]) -> None:
    fit = np.array([r["score"] for r in rows if r["fit"]])
    unfit = np.array([r["score"] for r in rows if not r["fit"]])

    print(f"\n{'=' * 66}\n점수 분포 (적합 {len(fit)}건 / 부적합 {len(unfit)}건)\n{'=' * 66}")
    for label, arr in [("적합", fit), ("부적합", unfit)]:
        if len(arr) == 0:
            print(f"  {label}: 없음")
            continue
        print(
            f"  {label:<5} 최소 {arr.min():>7.2f}  중앙 {np.median(arr):>7.2f}  "
            f"최대 {arr.max():>7.2f}"
        )

    if len(fit) and len(unfit):
        if fit.min() > unfit.max():
            print(
                f"\n  => 완전 분리. 적합 최소({fit.min():.2f}) > "
                f"부적합 최대({unfit.max():.2f})"
            )
        else:
            overlap = np.sum((unfit >= fit.min()) & (unfit <= fit.max()))
            print(
                f"\n  => **겹친다.** 부적합 {overlap}건이 적합 점수 구간 안에 있다 "
                f"(적합 최소 {fit.min():.2f} ≤ … ≤ 적합 최대 {fit.max():.2f})"
            )

    # 상대 격차 분포도 같이 — 절대값이 안 되면 이쪽이 답일 수 있다
    fit_gap = np.array([r["top1"] - r["score"] for r in rows if r["fit"]])
    unfit_gap = np.array([r["top1"] - r["score"] for r in rows if not r["fit"]])
    print("\n1위 대비 낙폭 (작을수록 1위에 가까움)")
    for label, arr in [("적합", fit_gap), ("부적합", unfit_gap)]:
        if len(arr):
            print(
                f"  {label:<5} 최소 {arr.min():>7.2f}  중앙 {np.median(arr):>7.2f}  "
                f"최대 {arr.max():>7.2f}"
            )


def sweep(rows: list[dict], mode: str) -> tuple[float, int] | None:
    """임계값을 훑는다. (적합을 안 자르는 가장 강한 임계값, 자른 부적합 수)."""
    thresholds = ABSOLUTE_FLOORS if mode == "absolute" else RELATIVE_GAPS
    label = "절대 하한 T" if mode == "absolute" else "상대 낙폭 D"

    print(f"\n{'=' * 66}\n{label} 훑기\n{'=' * 66}")
    print(f"{label:>12}{'적합 잘림':>10}{'부적합 잘림':>12}{'부적합 남음':>12}")

    best: tuple[float, int] | None = None
    for threshold in thresholds:
        if mode == "absolute":
            kept = [r["score"] >= threshold for r in rows]
        else:
            kept = [(r["top1"] - r["score"]) <= threshold for r in rows]

        fit_cut = sum(1 for r, k in zip(rows, kept) if r["fit"] and not k)
        unfit_cut = sum(1 for r, k in zip(rows, kept) if not r["fit"] and not k)
        unfit_kept = sum(1 for r, k in zip(rows, kept) if not r["fit"] and k)

        marker = ""
        if fit_cut == 0 and (best is None or unfit_cut > best[1]):
            best = (threshold, unfit_cut)
            marker = "  <-"
        print(
            f"{threshold:>12.1f}{fit_cut:>10}{unfit_cut:>12}{unfit_kept:>12}{marker}"
        )

    return best


def report_per_query(rows: list[dict], mode: str, threshold: float) -> None:
    print(f"\n{'=' * 66}\n질의별 결과 (채택 임계값 적용)\n{'=' * 66}")
    by_query: dict[str, list[dict]] = {}
    for row in rows:
        by_query.setdefault(row["query"].query, []).append(row)

    for query_text, group in by_query.items():
        query: FloorQuery = group[0]["query"]
        tag = ""
        if query.expect_all_fit:
            tag = " [통제군]"
        elif query.expect_none_fit:
            tag = " [정답 0건]"
        print(f"\n  {query_text}{tag}")
        for row in group:
            kept = (
                row["score"] >= threshold
                if mode == "absolute"
                else (row["top1"] - row["score"]) <= threshold
            )
            mark = "남김" if kept else "잘림"
            fit = "적합" if row["fit"] else "부적합"
            flag = ""
            if row["fit"] and not kept:
                flag = "  <- 적합을 잘랐다"
            print(
                f"    {row['name']:<22} {row['score']:>7.2f}  {fit}  {mark}{flag}"
            )


def score_at(rows: list[dict], mode: str, threshold: float) -> tuple[int, int, int]:
    """(적합 잘림, 부적합 잘림, 부적합 남음)."""
    if mode == "absolute":
        kept = [r["score"] >= threshold for r in rows]
    else:
        kept = [(r["top1"] - r["score"]) <= threshold for r in rows]
    return (
        sum(1 for r, k in zip(rows, kept) if r["fit"] and not k),
        sum(1 for r, k in zip(rows, kept) if not r["fit"] and not k),
        sum(1 for r, k in zip(rows, kept) if not r["fit"] and k),
    )


def calibrate(rows: list[dict], margin: float) -> dict:
    """한 번의 수집에서 임계값을 정하고 홀드아웃까지 검증한 결과.

    **그리디로 고르지 않는다.** "부적합을 가장 많이 자르는" 값을 집으면
    튜닝셋의 적합 경계에 딱 붙어서 홀드아웃에서 적합을 자른다(실측 확인).
    대신 튜닝셋 적합 경계에서 마진만큼 물러선다.
    """
    tune = [r for r in rows if not r["query"].holdout]
    holdout = [r for r in rows if r["query"].holdout]

    tune_fit_scores = [r["score"] for r in tune if r["fit"]]
    tune_fit_gaps = [r["top1"] - r["score"] for r in tune if r["fit"]]
    if not tune_fit_scores:
        return {"usable": None, "plans": {}}

    plans = {
        "absolute": min(tune_fit_scores) - margin,
        "relative": max(tune_fit_gaps) + margin,
    }

    result: dict = {"plans": {}, "usable": None}
    for mode, threshold in plans.items():
        t_fit, t_unfit, _ = score_at(tune, mode, threshold)
        h_fit, h_unfit, _ = score_at(holdout, mode, threshold)
        result["plans"][mode] = {
            "threshold": threshold,
            "tune_fit_cut": t_fit,
            "tune_unfit_cut": t_unfit,
            "holdout_fit_cut": h_fit,
            "holdout_unfit_cut": h_unfit,
            "ok": h_fit == 0 and h_unfit > 0,
        }

    ok = [m for m, p in result["plans"].items() if p["ok"]]
    if ok:
        result["usable"] = max(ok, key=lambda m: result["plans"][m]["holdout_unfit_cut"])
    return result


def report_spread(runs: list[list[dict]]) -> None:
    """(a) 점수 산포와 (d) 순위 역전.

    산포는 **마진과 같은 단위**라 직접 비교할 수 있다. 지난번엔 산포 0.5~1.3에
    마진 0.3~0.6이라 신호보다 노이즈가 컸다.
    """
    # (질의, 아이템) -> 실행별 점수. 실행마다 결과 집합이 달라질 수 있어
    # 등장 횟수도 같이 센다 — 어떤 실행에만 나온 아이템은 그 자체가 불안정이다.
    scores: dict[tuple[str, str], list[float]] = {}
    for rows in runs:
        for row in rows:
            scores.setdefault((row["query"].query, row["name"]), []).append(row["score"])

    n = len(runs)
    spreads = [(k, max(v) - min(v)) for k, v in scores.items() if len(v) == n]
    missing = [k for k, v in scores.items() if len(v) != n]

    print(f"\n{'=' * 72}\n(a) 점수 산포 - {n}회 반복\n{'=' * 72}")
    if spreads:
        values = np.array([s for _, s in spreads])
        print(
            f"  전 실행에 등장한 (질의,아이템) {len(spreads)}건: "
            f"중앙 {np.median(values):.3f}  평균 {values.mean():.3f}  "
            f"**최대 {values.max():.3f}**"
        )
        for (query, name), spread in sorted(spreads, key=lambda e: -e[1])[:5]:
            if spread > 0:
                print(f"    {spread:>6.3f}  {query} / {name}")
        if values.max() == 0:
            print("    => 모든 아이템의 점수가 실행마다 완전히 동일했다.")
    if missing:
        print(f"\n  일부 실행에만 등장한 (질의,아이템) {len(missing)}건 - 이것도 불안정이다:")
        for query, name in missing[:8]:
            print(f"    {len(scores[(query, name)])}/{n}회  {query} / {name}")

    # 질의 내 마진 = 적합 최솟값 − 부적합 최댓값. 이게 산포보다 커야 한다.
    print(f"\n{'=' * 72}\n질의 내 마진 (적합 최솟값 − 부적합 최댓값)\n{'=' * 72}")
    margins: list[float] = []
    inversions: list[tuple[str, str, str]] = []
    # **뺀 질의를 센다.** 마진은 "적합 최솟값 − 부적합 최댓값"이라 한쪽이 없으면
    # 계산할 수 없는데, 예전 판본은 그냥 `continue` 했다. 그러면 아래 표에 안
    # 보이고 `최소 마진`의 분모도 안 보인다 — **판정에 쓴 값을 전부 출력한다**는
    # 이 저장소의 규칙에 걸린다(ADR-0053).
    dropped: dict[str, str] = {}
    for index, rows in enumerate(runs, start=1):
        by_query: dict[str, list[dict]] = {}
        for row in rows:
            by_query.setdefault(row["query"].query, []).append(row)
        for query, group in by_query.items():
            fit = [r["score"] for r in group if r["fit"]]
            unfit = [r["score"] for r in group if not r["fit"]]
            if not fit or not unfit:
                if index == 1:
                    dropped[query] = "부적합 없음" if fit else "적합 없음"
                continue
            margin = min(fit) - max(unfit)
            if index == 1:
                margins.append(margin)
                print(f"  {query:<26} {margin:>+7.2f}")
            elif margin not in margins:
                margins.append(margin)
            # 순위 역전: 적합보다 점수가 높은 부적합
            for row in group:
                if not row["fit"] and row["score"] > min(fit):
                    inversions.append((f"실행{index}", query, row["name"]))

    if dropped:
        print(
            f"\n  !! 마진을 못 재서 뺀 질의 {len(dropped)}건 "
            f"— 아래 `최소 마진`의 분모는 나머지다:"
        )
        for query, reason in dropped.items():
            print(f"       {query}  ({reason})")

    if margins:
        print(
            f"\n  최소 마진 {min(margins):+.2f}  "
            f"(전 실행 통합, 질의 {len(margins) if len(runs) == 1 else '중복 제거 후 ' + str(len(margins))}개 기준)"
        )
        if spreads:
            worst = max(s for _, s in spreads)
            verdict = "충족" if worst < min(margins) else "**미충족**"
            print(
                f"\n  => 전제 조건 (최대 산포 {worst:.3f} < 최소 마진 "
                f"{min(margins):.2f}): {verdict}"
            )

    print(f"\n{'=' * 72}\n(d) 순위 역전 - 부적합이 적합보다 위에 있는가\n{'=' * 72}")
    if not inversions:
        print("  없음. 질의별로 적합이 전부 부적합보다 위에 있다.")
    else:
        seen = {(q, n) for _, q, n in inversions}
        print(f"  {len(seen)}건 (하한으로는 **원리적으로** 못 푸는 케이스):")
        for query, name in sorted(seen):
            print(f"    {query} / {name}")


def report_reproducibility(results: list[dict]) -> None:
    """(b) 결론이 실행마다 같은가 — 지난번 깨진 지점."""
    print(f"\n{'=' * 72}\n(b) 재현성 - 실행별 임계값 산정 결과\n{'=' * 72}")
    print(
        f"{'실행':<6}{'방식':<10}{'임계값':>9}{'튜닝 적합잘림':>13}"
        f"{'튜닝 부적합잘림':>15}{'홀드 적합잘림':>13}{'홀드 부적합잘림':>15}"
    )
    for index, result in enumerate(results, start=1):
        for mode, plan in result["plans"].items():
            flag = "" if plan["holdout_fit_cut"] == 0 else "  <- 적합 손실"
            print(
                f"{index:<6}{mode:<10}{plan['threshold']:>9.2f}"
                f"{plan['tune_fit_cut']:>13}{plan['tune_unfit_cut']:>15}"
                f"{plan['holdout_fit_cut']:>13}{plan['holdout_unfit_cut']:>15}{flag}"
            )

    verdicts = [result["usable"] for result in results]
    unique = set(verdicts)
    print(f"\n  실행별 결론: {verdicts}")
    if len(unique) == 1:
        only = verdicts[0]
        print(
            "  => **일치한다.** "
            + (
                f"모든 실행에서 `{only}` 방식이 홀드아웃 적합 손실 0."
                if only
                else "모든 실행에서 **쓸 수 있는 임계값이 없다.**"
            )
        )
    else:
        print(
            "  => **일치하지 않는다.** 실행마다 다른 결론이 나온다 — "
            "지난번(2026-07-30)과 같은 실패다."
        )


def report_split_stability(runs: list[list[dict]], margin: float) -> None:
    """분할 의존성 — 절대 하한의 안전성이 튜닝/홀드아웃 분할 운에 기대는가.

    ## 왜 필요한가

    반복 실행은 **같은 분할**을 되풀이한 것이라, "노이즈가 줄었다"와 "이 분할에서
    우연히 여유가 생겼다"를 구분하지 못한다. 채택된 임계값이 홀드아웃 적합을
    안 자른 이유는 튜닝셋 적합 최솟값이 홀드아웃보다 **낮았기 때문**인데,
    2026-07-30에는 정확히 반대였다(튜닝 −3.59 vs 홀드아웃 −4.10).

    ## 재는 것

    핵심 통계는 **Δ = 홀드아웃 적합 최솟값 − 튜닝 적합 최솟값**이다.

    - `Δ > 0` — 튜닝셋이 더 낮은 적합을 품고 있다. 마진 없이도 안전하다
    - `Δ < 0` — 홀드아웃에 더 낮은 적합이 있다. 마진이 `|Δ|`보다 커야 산다

    가능한 분할을 **전수 열거**한다. API 호출은 필요 없다 — 이미 수집한 점수를
    질의 단위로 다시 나누기만 하면 된다. 실제 쓰는 분할이 그 분포에서 어디쯤인지
    (백분위)까지 보면 "우연이었나"에 답할 수 있다.

    **결과 0건 질의는 행을 남기지 않으므로 열거에서 빠진다.** 지금은
    `"3만원 이하 불속성 검"` 한 건이라 9질의를 4/5로 나눈다.
    """
    print(f"\n{'=' * 72}\n분할 안정성 - 튜닝/홀드아웃 분할 전수 열거\n{'=' * 72}")

    for run_index, rows in enumerate(runs, start=1):
        fit_by_query: dict[str, list[float]] = {}
        unfit_by_query: dict[str, list[float]] = {}
        for row in rows:
            target = fit_by_query if row["fit"] else unfit_by_query
            target.setdefault(row["query"].query, []).append(row["score"])

        queries = sorted({row["query"].query for row in rows})
        # 실제 쓰는 분할도 "행을 남긴 질의"로만 좁혀야 열거된 분할과 비교된다.
        designed = frozenset(
            q.query for q in FLOOR_QUERIES if not q.holdout
        ) & frozenset(queries)
        tune_size = len(designed)

        records: list[dict] = []
        skipped = 0

        for tune_queries in combinations(queries, tune_size):
            tune_set = frozenset(tune_queries)
            hold_set = frozenset(queries) - tune_set

            tune_fit = [s for q in tune_set for s in fit_by_query.get(q, [])]
            hold_fit = [s for q in hold_set for s in fit_by_query.get(q, [])]
            hold_unfit = [s for q in hold_set for s in unfit_by_query.get(q, [])]
            if not tune_fit or not hold_fit:
                skipped += 1  # 한쪽에 적합이 없으면 캘리브레이션 자체가 불가
                continue

            threshold = min(tune_fit) - margin
            records.append(
                {
                    "tune": tune_set,
                    "delta": min(hold_fit) - min(tune_fit),
                    "threshold": threshold,
                    "fit_cut": sum(1 for s in hold_fit if s < threshold),
                    "unfit_cut": sum(1 for s in hold_unfit if s < threshold),
                    "headroom": min(hold_fit) - threshold,
                }
            )

        deltas = np.array([r["delta"] for r in records])
        safe = [r for r in records if r["fit_cut"] == 0]
        positive = int((deltas > 0).sum())

        print(
            f"\n[{run_index}회차 수집] 질의 {len(queries)}건을 "
            f"{tune_size}/{len(queries) - tune_size}로 — 유효 분할 "
            f"{len(records)}가지 (제외 {skipped})"
        )
        print(
            f"  Δ = 홀드아웃 적합 최솟값 − 튜닝 적합 최솟값\n"
            f"    최소 {deltas.min():+.2f}  중앙 {np.median(deltas):+.2f}  "
            f"최대 {deltas.max():+.2f}"
        )
        print(
            f"    **부등호 유지(Δ>0): {positive}/{len(records)} "
            f"({positive / len(records):.0%})**"
        )
        print(
            f"  마진 {margin} 적용 시 홀드아웃 적합 손실 0: "
            f"**{len(safe)}/{len(records)} ({len(safe) / len(records):.0%})**"
        )
        if safe:
            benefits = np.array([r["unfit_cut"] for r in safe])
            print(
                f"    그중 홀드아웃 부적합 제거: 중앙 {np.median(benefits):.0f}건 "
                f"(0건인 분할 {int((benefits == 0).sum())}가지)"
            )

        # 실제 쓰는 분할이 이 분포에서 어디인가
        actual = next((r for r in records if r["tune"] == designed), None)
        if actual:
            percentile = float((deltas <= actual["delta"]).mean() * 100)
            print(
                f"\n  실제 분할: Δ {actual['delta']:+.2f}  T {actual['threshold']:.2f}  "
                f"적합잘림 {actual['fit_cut']}  부적합잘림 {actual['unfit_cut']}"
            )
            print(f"    Δ 백분위 **{percentile:.0f}%** - 100%에 가까울수록 운이 좋았던 것")

        worst = sorted(records, key=lambda r: r["delta"])[:3]
        print("\n  Δ가 가장 나쁜 분할 3가지 (튜닝셋에 들어간 질의):")
        for record in worst:
            print(
                f"    Δ {record['delta']:+.2f}  적합잘림 {record['fit_cut']}  "
                f"튜닝={sorted(record['tune'])}"
            )

        # 분할 전체에서 안전한 마진이 존재하는가. 마진을 키우면 안전해지지만
        # 임계값이 같이 내려가 아무것도 못 자르게 된다 — 그 상충을 직접 본다.
        if run_index == 1:
            print("\n  마진을 키우면 분할 전체에서 안전해지는가:")
            print(f"    {'마진':>6}{'적합손실 0인 분할':>18}{'부적합 제거 중앙':>18}")
            for candidate in (0.5, 1.0, 1.5, 2.0, 3.0, 4.0):
                safe_at = []
                for record in records:
                    threshold = record["threshold"] + margin - candidate
                    tune_set = record["tune"]
                    hold_set = frozenset(queries) - tune_set
                    hold_fit = [s for q in hold_set for s in fit_by_query.get(q, [])]
                    hold_unfit = [s for q in hold_set for s in unfit_by_query.get(q, [])]
                    if any(s < threshold for s in hold_fit):
                        continue
                    safe_at.append(sum(1 for s in hold_unfit if s < threshold))
                share = len(safe_at) / len(records)
                median_cut = np.median(safe_at) if safe_at else 0
                print(
                    f"    {candidate:>6.1f}{len(safe_at):>10}/{len(records):<7}"
                    f"({share:>4.0%}){median_cut:>12.0f}건"
                )


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--size", type=int, default=8)
    parser.add_argument("--runs", type=int, default=5, help="같은 절차를 몇 회 반복할지")
    parser.add_argument(
        "--splits",
        action="store_true",
        help="5/5 분할을 전수 열거해 분할 의존성을 본다 (추가 API 호출 없음)",
    )
    parser.add_argument(
        "--margin",
        type=float,
        default=0.5,
        help="튜닝셋 적합 최솟값에서 얼마나 물러설지. 0이면 그리디와 같아진다",
    )
    args = parser.parse_args()

    tune_count = sum(1 for q in FLOOR_QUERIES if not q.holdout)
    print(
        f"질의 {len(FLOOR_QUERIES)}건 (튜닝 {tune_count} / 홀드아웃 "
        f"{len(FLOOR_QUERIES) - tune_count}), 질의당 상위 {args.size}건, "
        f"{args.runs}회 반복"
    )

    runs = [await collect(args.size) for _ in range(args.runs)]
    first = runs[0]
    tune = [r for r in first if not r["query"].holdout]
    holdout = [r for r in first if r["query"].holdout]
    print(
        f"수집 {len(first)}건 (튜닝 {len(tune)} / 홀드아웃 {len(holdout)}), "
        f"튜닝 부적합 {sum(1 for r in tune if not r['fit'])}건"
    )

    print("\n[1회차 전체 분포]")
    report_distribution(first)

    report_spread(runs)

    results = [calibrate(rows, args.margin) for rows in runs]
    report_reproducibility(results)

    if args.splits:
        report_split_stability(runs, args.margin)

    # 훑기는 참고용으로 1회차만. 결론은 위 마진 기반 산정이 낸다.
    print("\n[1회차 임계값 훑기 - 참고]")
    sweep(tune, "absolute")
    sweep(tune, "relative")

    usable = results[0]["usable"]
    if usable and len({r["usable"] for r in results}) == 1:
        threshold = results[0]["plans"][usable]["threshold"]
        print(f"\n  => 채택 가능: {usable} {threshold:.2f}")
        report_per_query(first, usable, threshold)
    else:
        print(
            "\n  => **채택하지 않는다.** 홀드아웃 적합 손실이 0인 방식이 "
            "없거나 실행마다 결론이 달라진다."
        )


if __name__ == "__main__":
    asyncio.run(main())
