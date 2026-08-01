"""LLM 생성 질의셋 vs 수동 작성 질의셋으로 파인튜닝 효과를 교차 검증한다.

**무엇을 확인하려는 것인가**

파인튜닝 개선분이 "아이템 의미를 배운 것"인지 "학습 anchor의 질의 스타일에
적응한 것"인지 구분하려는 것이다. 학습 anchor와 기존 평가 질의는 둘 다 비슷한
프롬프트로 LLM이 만들었기 때문에, 스타일 적응만으로도 수치가 오를 수 있다.

그래서 LLM이 절대 만들지 않는 형식(키워드 나열, 오타, 초성, 붙여쓰기)으로
수동 작성한 질의를 같은 홀드아웃 코퍼스에 돌린다.

- 두 셋에서 **개선 방향이 같으면** 스타일 적응만은 아니라는 근거가 된다
- 수동셋에서 **개선이 사라지거나 역전되면** 스타일 과적합 증거다

주의: 두 셋은 스타일 외에도 "아이템명과의 어휘 중첩" 정도가 다르다. 키워드
나열형은 아이템명 조각을 그대로 포함하는 반면 LLM 질의는 이름 복사를 금지당했다.
따라서 **절대 수치를 직접 비교하는 건 부적절**하고, 각 셋 내부의 전/후 변화량을
비교해야 한다.

실행: python -m scripts.compare_eval_sets
"""

from __future__ import annotations

import argparse
import io
import json
from collections import defaultdict
from pathlib import Path

from app.corpus import ALL_ITEMS
from app.core.config import get_settings
from app.services.training.evaluation import evaluate_model, ir_metrics
from app.services.training.hard_negatives import item_text


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in io.open(path, encoding="utf-8")]


def print_set(label: str, before: dict, after: dict) -> None:
    print(f"\n### {label}")
    print(f"{'지표':<12} {'전':>9} {'후':>9} {'변화':>9}")
    print("-" * 42)
    for key in ("recall@1", "recall@3", "recall@5", "mrr", "mean_rank"):
        delta = after[key] - before[key]
        print(f"{key:<12} {before[key]:>9.4f} {after[key]:>9.4f} {delta:>+9.4f}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--llm-set", default="data/eval_queries.jsonl")
    parser.add_argument("--manual-set", default="data/eval_queries_manual.jsonl")
    parser.add_argument("--finetuned", default="models/embedding-finetuned")
    args = parser.parse_args()

    from sentence_transformers import SentenceTransformer

    settings = get_settings()
    corpus_texts = [item_text(i) for i in ALL_ITEMS]
    id_to_index = {item["item_id"]: idx for idx, item in enumerate(ALL_ITEMS)}

    # evaluate_embedding 과 같은 이유로 베이스는 스톡 모델이다.
    base = SentenceTransformer(settings.embedding_base_model)
    tuned = SentenceTransformer(args.finetuned)

    results = {}
    for label, path in [("LLM 생성", args.llm_set), ("수동 작성", args.manual_set)]:
        rows = load(Path(path))
        queries = [r["query"] for r in rows]
        gold = [id_to_index[r["gold_item_id"]] for r in rows]

        before, rank_before = evaluate_model(base, queries, gold, corpus_texts)
        after, rank_after = evaluate_model(tuned, queries, gold, corpus_texts)
        results[label] = (rows, before, after, rank_before, rank_after, gold)

        print(f"\n{'='*52}")
        print(f"{label} 질의셋 — {len(queries)}건")
        print_set(label, before, after)

    # 스타일별 세부 (수동셋만 style 태그가 있다)
    rows, _, _, rank_before, rank_after, gold = results["수동 작성"]
    if rows and "style" in rows[0]:
        by_style: dict[str, list[int]] = defaultdict(list)
        for idx, row in enumerate(rows):
            by_style[row["style"]].append(idx)

        print(f"\n{'='*52}")
        print("수동셋 스타일별 (건수 적으니 경향만 참고)")
        print(f"{'스타일':<10} {'n':>3} {'MRR 전':>9} {'MRR 후':>9} {'변화':>9}")
        print("-" * 45)
        for style, indices in sorted(by_style.items()):
            b = ir_metrics(rank_before[indices], [gold[i] for i in indices])
            a = ir_metrics(rank_after[indices], [gold[i] for i in indices])
            print(
                f"{style:<10} {len(indices):>3} {b['mrr']:>9.4f} {a['mrr']:>9.4f} "
                f"{a['mrr'] - b['mrr']:>+9.4f}"
            )

    # 판정
    llm_delta = results["LLM 생성"][2]["mrr"] - results["LLM 생성"][1]["mrr"]
    man_delta = results["수동 작성"][2]["mrr"] - results["수동 작성"][1]["mrr"]
    print(f"\n{'='*52}")
    print(f"MRR 개선폭:  LLM 생성 {llm_delta:+.4f}   수동 작성 {man_delta:+.4f}")
    if man_delta > 0 and llm_delta > 0:
        ratio = man_delta / llm_delta if llm_delta else float("inf")
        print(
            f"두 셋 모두 개선 (수동셋이 LLM셋의 {ratio:.0%} 수준) — "
            "질의 스타일 적응만으로는 설명되지 않음"
        )
    elif man_delta <= 0 < llm_delta:
        print("수동셋에서 개선이 사라짐 — 질의 스타일 과적합 의심")
    else:
        print("해석 주의: 두 셋의 방향이 엇갈림")

    # 개별 결과 (수동셋은 작으니 전부 보여준다)
    print(f"\n{'='*52}")
    print("수동셋 개별 결과 (정답 순위)")
    for idx, row in enumerate(rows):
        rb = int((rank_before[idx] == gold[idx]).argmax()) + 1
        ra = int((rank_after[idx] == gold[idx]).argmax()) + 1
        arrow = "->" if rb == ra else ("v" if ra < rb else "^")
        print(
            f"  [{row['style']:<8}] {row['query']:<18} {rb:>3} {arrow} {ra:<3}  "
            f"({row['gold_name']})"
        )


if __name__ == "__main__":
    main()
