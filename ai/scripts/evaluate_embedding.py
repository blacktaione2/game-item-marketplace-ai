"""파인튜닝 전/후 검색 품질 비교 — **EVAL_ITEMS 질의로만** 평가한다.

두 종류의 지표를 낸다:
  1. IR 지표(Recall@k, MRR) — 결정론적, 기준점
  2. RAGAS(context precision/recall) — 계획서가 지정한 표준, LLM 심판

검색 코퍼스는 train + eval 전체다. 평가 질의의 정답은 항상 eval 아이템이지만,
train 아이템이 방해 문서로 섞여 있어야 현실적인 난이도가 된다.

실행:
  python -m scripts.evaluate_embedding                # IR 지표만 (LLM 호출 없음)
  python -m scripts.evaluate_embedding --ragas        # RAGAS까지
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
from pathlib import Path

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


async def run_ragas(
    queries: list[str],
    gold_texts: list[str],
    contexts_before: list[list[str]],
    contexts_after: list[list[str]],
) -> None:
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

    print("\nRAGAS 평가 중 (LLM 심판, 질의당 2회 호출 x 2모델)...")
    before = await score(contexts_before)
    after = await score(contexts_after)

    print(f"\n{'RAGAS 지표':<20} {'전':>10} {'후':>10} {'변화':>10}")
    print("-" * 54)
    for key in ("context_precision", "context_recall"):
        delta = after[key] - before[key]
        print(f"{key:<20} {before[key]:>10.4f} {after[key]:>10.4f} {delta:>+10.4f}")
    print(f"(채점 성공 표본: 전 {before['n_precision']}/{len(queries)}, 후 {after['n_precision']}/{len(queries)})")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--eval-queries", default="data/eval_queries.jsonl")
    parser.add_argument("--finetuned", default="models/embedding-finetuned")
    parser.add_argument("--top-k", type=int, default=5, help="RAGAS에 넘길 컨텍스트 수")
    parser.add_argument("--ragas", action="store_true")
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
        return

    tuned = SentenceTransformer(str(tuned_path))
    after, rank_after = evaluate_model(tuned, queries, gold_indices, corpus_texts)

    print_table(before, after)

    if args.ragas:
        contexts_before = [
            [corpus_texts[i] for i in row[: args.top_k]] for row in rank_before
        ]
        contexts_after = [
            [corpus_texts[i] for i in row[: args.top_k]] for row in rank_after
        ]
        gold_texts = [corpus_texts[g] for g in gold_indices]
        asyncio.run(run_ragas(queries, gold_texts, contexts_before, contexts_after))


if __name__ == "__main__":
    main()
