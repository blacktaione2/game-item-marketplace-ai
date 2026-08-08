"""파인튜닝 전/후 검색 품질 비교 — **EVAL_ITEMS 질의로만** 평가한다.

두 종류의 지표를 낸다:
  1. IR 지표(Recall@k, MRR) — 결정론적, 기준점
  2. RAGAS(context precision/recall) — 계획서가 지정한 표준, LLM 심판

검색 코퍼스는 train + eval 전체다. 평가 질의의 정답은 항상 eval 아이템이지만,
train 아이템이 방해 문서로 섞여 있어야 현실적인 난이도가 된다.

실행:
  python -m scripts.evaluate_embedding                # IR 지표만 (LLM 호출 없음)
  python -m scripts.evaluate_embedding --ragas        # RAGAS까지
  python -m scripts.evaluate_embedding --json out.json  # 기계 판독용 (CI 게이트)

**수집과 채점은 분리한다.** 이 스크립트는 수치를 내기만 하고 합격/불합격을
판단하지 않는다. 판단은 `scripts/check_ir_gate.py` 가 이 `--json` 출력을 읽어
한다. 그래야 문턱을 고칠 때 재학습·재평가를 다시 하지 않는다 — 이 저장소가
프롬프트 평가에서 이미 쓰는 규칙과 같다(`--score-only`).
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
from pathlib import Path
from typing import Any

from app.corpus import ALL_ITEMS
from app.core.config import get_settings
from app.services.training.evaluation import compare, evaluate_model
from app.services.training.hard_negatives import item_text


def load_eval_queries(path: Path) -> list[dict]:
    return [json.loads(line) for line in io.open(path, encoding="utf-8")]


def print_table(before: dict, after: dict) -> None:
    print(f"\n{'지표':<12} {'파인튜닝 전':>12} {'파인튜닝 후':>12} {'변화':>10}")
    print("-" * 50)
    for row in compare(before, after):
        mark = "" if row["improved"] is None else (" +" if row["improved"] else " -")
        print(
            f"{row['metric']:<12} {row['before']:>12.4f} {row['after']:>12.4f} "
            f"{row['delta']:>+10.4f}{mark}"
        )


# 채점 실패가 이 비율을 넘으면 **점수를 내지 않는다.** 실패한 호출은 답이 아니라
# 결측이고, 성공분만 평균 내면 표본 절반이 사라진 채 그럴듯한 숫자가 남는다
# (사례집 19). `evaluate_element_extraction.py` 가 쓰는 것과 같은 장치다.
RAGAS_MAX_ERROR_RATE = 0.10


async def run_ragas(
    queries: list[str],
    gold_texts: list[str],
    contexts_before: list[list[str]],
    contexts_after: list[list[str]],
) -> dict[str, Any]:
    from ragas.llms import llm_factory
    from ragas.metrics.collections import ContextPrecisionWithReference, ContextRecall

    from openai import AsyncOpenAI

    settings = get_settings()
    # ragas 0.4.x는 client 인스턴스를 요구한다(text-only 모드 제거됨).
    client = AsyncOpenAI(api_key=settings.openai_api_key)
    llm = llm_factory(settings.openai_model, client=client)

    precision = ContextPrecisionWithReference(llm=llm)
    recall = ContextRecall(llm=llm)

    async def score(contexts: list[list[str]]) -> dict[str, float]:
        p_scores, r_scores = [], []
        for query, gold, ctx in zip(queries, gold_texts, contexts):
            try:
                p = await precision.ascore(
                    user_input=query, reference=gold, retrieved_contexts=ctx
                )
                p_scores.append(float(p.value))
            except Exception as e:
                print(f"  context_precision 실패: {e}")
            try:
                r = await recall.ascore(
                    user_input=query, retrieved_contexts=ctx, reference=gold
                )
                r_scores.append(float(r.value))
            except Exception as e:
                print(f"  context_recall 실패: {e}")
        return {
            "context_precision": round(sum(p_scores) / len(p_scores), 4)
            if p_scores
            else float("nan"),
            "context_recall": round(sum(r_scores) / len(r_scores), 4)
            if r_scores
            else float("nan"),
            "n_precision": len(p_scores),
            "n_recall": len(r_scores),
        }

    # **호출 수와 소요를 미리 밝힌다.** `ContextPrecisionWithReference` 는
    # 검색된 컨텍스트마다 1회씩 순차 호출하므로, 질의 수만 보고 어림하면 3배
    # 틀린다(실측: 질의 54 · top-k 5 에서 약 648회, 119분).
    n_calls = len(queries) * (len(contexts_before[0]) + 1) * 2
    print(f"\nRAGAS 평가 중 - LLM 심판 약 {n_calls}회 (순차). 수십 분 걸린다...")
    before = await score(contexts_before)
    after = await score(contexts_after)

    print(f"\n{'RAGAS 지표':<20} {'전':>10} {'후':>10} {'변화':>10}")
    print("-" * 54)
    for key in ("context_precision", "context_recall"):
        delta = after[key] - before[key]
        print(f"{key:<20} {before[key]:>10.4f} {after[key]:>10.4f} {delta:>+10.4f}")
    print(f"(채점 성공 표본: 전 {before['n_precision']}/{len(queries)}, 후 {after['n_precision']}/{len(queries)})")

    # **실패율이 높으면 점수를 신뢰하지 않는다.** 성공분만 평균 내면 표본이
    # 절반 사라진 채로 그럴듯한 숫자가 남는다.
    worst = min(before["n_precision"], after["n_precision"],
                before["n_recall"], after["n_recall"])
    error_rate = 1 - worst / len(queries)
    trustworthy = error_rate <= RAGAS_MAX_ERROR_RATE
    if not trustworthy:
        print(f"\n[경고] 채점 실패율 {error_rate:.1%} > {RAGAS_MAX_ERROR_RATE:.0%} "
              "— 위 점수를 근거로 쓰지 말 것")

    return {
        "before": before,
        "after": after,
        "n_calls_estimated": n_calls,
        "error_rate": round(error_rate, 4),
        "trustworthy": trustworthy,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--eval-queries", default="data/eval_queries.jsonl")
    parser.add_argument("--finetuned", default="models/embedding-finetuned")
    parser.add_argument("--top-k", type=int, default=5, help="RAGAS에 넘길 컨텍스트 수")
    parser.add_argument("--ragas", action="store_true")
    parser.add_argument("--json", help="지표를 이 경로에 JSON으로 저장 (CI 게이트용)")
    args = parser.parse_args()

    from sentence_transformers import SentenceTransformer

    settings = get_settings()
    rows = load_eval_queries(Path(args.eval_queries))
    queries = [r["query"] for r in rows]

    corpus_texts = [item_text(i) for i in ALL_ITEMS]
    id_to_index = {item["item_id"]: idx for idx, item in enumerate(ALL_ITEMS)}
    gold_indices = [id_to_index[r["gold_item_id"]] for r in rows]

    print(
        f"평가 질의 {len(queries)}건 / 검색 코퍼스 {len(corpus_texts)}건 "
        f"(train {len(ALL_ITEMS) - 18} + eval 18)"
    )
    print("정답은 전부 eval 아이템, train 아이템은 방해 문서로만 존재")

    # **`settings.embedding_model` 을 쓰면 안 된다** — 그건 파인튜닝 결과를
    # 가리키므로 아래 `tuned` 와 같은 모델이 되고, before-after 가 자기 자신과의
    # 비교가 된다(개선폭이 0으로 나온다). 이 상태로 커밋돼 있었다.
    base = SentenceTransformer(settings.embedding_base_model)
    before, rank_before = evaluate_model(base, queries, gold_indices, corpus_texts)

    tuned_path = Path(args.finetuned)
    if not tuned_path.exists():
        print(f"\n파인튜닝 모델이 없습니다: {tuned_path}")
        print("먼저 python -m scripts.finetune_embedding 을 실행하세요.")
        print(f"\n파인튜닝 전 지표: {before}")
        # **0으로 끝내면 안 된다.** CI가 이걸 호출하는데 "모델이 없다"가 통과로
        # 읽히면 게이트가 통째로 공허해진다 — 이 저장소가 이미 두 번 겪은
        # "안 뜬 것을 닫힌 것으로 읽었다"와 같은 형태다(사례집 5·11).
        raise SystemExit(1)

    tuned = SentenceTransformer(str(tuned_path))
    after, rank_after = evaluate_model(tuned, queries, gold_indices, corpus_texts)

    print_table(before, after)

    payload: dict[str, Any] = {
        "n_queries": len(queries),
        "n_corpus": len(corpus_texts),
        "base_model": settings.embedding_base_model,
        "tuned_path": str(tuned_path),
        "before": before,
        "after": after,
    }

    # **RAGAS 를 JSON 저장보다 먼저 돌린다.** 순서를 반대로 두면 두 시간짜리
    # 채점 결과가 로그에만 남는다 — CI 로그는 공개 저장소여도 토큰이 있어야
    # 읽히므로, 사실상 아무도 못 본다. 리포트가 안 읽히면 리포트가 아니다.
    if args.ragas:
        contexts_before = [
            [corpus_texts[i] for i in row[: args.top_k]] for row in rank_before
        ]
        contexts_after = [
            [corpus_texts[i] for i in row[: args.top_k]] for row in rank_after
        ]
        gold_texts = [corpus_texts[g] for g in gold_indices]
        payload["ragas"] = asyncio.run(
            run_ragas(queries, gold_texts, contexts_before, contexts_after)
        )
        _write_step_summary(payload["ragas"])

    if args.json:
        Path(args.json).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n지표 저장: {args.json}")


def _write_step_summary(ragas: dict[str, Any]) -> None:
    """GitHub Actions 실행 요약에 RAGAS 표를 붙인다 (설정돼 있을 때만).

    게이트 쪽(`check_ir_gate.py`)이 여유폭 표를 붙이는 것과 같은 이유다 —
    **아티팩트도 로그도 토큰 없이는 못 읽는다.**
    """
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    b, a = ragas["before"], ragas["after"]
    lines = [
        "### RAGAS 리포트 (게이트 아님)",
        "",
        f"LLM 심판 약 {ragas['n_calls_estimated']}회 · 채점 실패율 "
        f"{ragas['error_rate']:.1%}"
        + ("" if ragas["trustworthy"] else "  **← 실패율이 높아 신뢰할 수 없다**"),
        "",
        "| 지표 | 파인튜닝 전 | 후 | 변화 |",
        "|---|---|---|---|",
    ]
    for key in ("context_precision", "context_recall"):
        lines.append(f"| `{key}` | {b[key]:.4f} | **{a[key]:.4f}** | {a[key] - b[key]:+.4f} |")
    lines.append("")
    lines.append("판정에는 쓰지 않는다 — 심판 변동폭이 특성화되지 않았다 (ADR-0043).")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
