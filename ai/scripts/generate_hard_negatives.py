"""Hard Negative Mining용 학습 트리플을 자동 생성한다.

출력은 sentence-transformers가 바로 먹을 수 있는 JSONL
(anchor / positive / negative) 형식이다.

실행:
  python -m scripts.generate_hard_negatives                    # 전체, LLM 사용
  python -m scripts.generate_hard_negatives --dry-run          # 규칙만, LLM 호출 없음
  python -m scripts.generate_hard_negatives --limit 3          # 앞 3건만 (비용 확인용)
  python -m scripts.generate_hard_negatives --out data/train.jsonl

## 이 스크립트만 temperature가 0이 아니다

`settings.openai_temperature`는 0이다 — 서비스 경로에서는 실행마다 답이 달라지면
측정이 깨지기 때문이다(ADR-0017). **여기는 반대다.** LLM에게 맡긴 몫이
"현실적인 사용자 표현"과 "의미적 near-miss"를 만드는 것이라 **다양성이 곧
데이터 품질**이고, 0으로 두면 아이템마다 비슷한 문장만 나와 트리플이 빈약해진다.

그래서 전역 설정을 쓰지 않고 자기 클라이언트를 따로 만든다(`--temperature`).
**"일관성"을 이유로 이걸 0으로 되돌리지 말 것** — 그건 여기서 개선이 아니다.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import random
from pathlib import Path

from app.core.config import get_settings
from app.services.llm.openai_client import OpenAIClient
from app.services.training.hard_negatives import (
    build_triplets,
    generate_llm_pairs,
    mine_corpus_negatives,
    mine_structural_negatives,
    normalize_name,
    pick_easy_negative,
    quality_report,
    sanitize_synthetic,
)
# 트리플은 **학습용 아이템으로만** 만든다. 평가 아이템이 섞이면 홀드아웃이
# 오염되어 파인튜닝 전/후 비교가 무의미해진다.
from app.corpus import EVAL_ITEMS
from app.corpus import TRAIN_ITEMS as ITEMS


def fallback_queries(item: dict) -> list[str]:
    """LLM 없이 돌릴 때(--dry-run) 쓰는 최소한의 anchor.

    학습용으로 쓸 만한 품질은 아니고, 규칙 기반 마이닝 로직만 점검하기 위한 것.
    """
    name = item["name"]
    return [name, f"{item['category']} {name}"]


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="data/train_triplets.jsonl")
    parser.add_argument("--queries-per-item", type=int, default=3)
    parser.add_argument("--negatives-per-item", type=int, default=3)
    parser.add_argument("--limit", type=int, default=None, help="처리할 아이템 수 상한")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--dry-run", action="store_true", help="LLM을 호출하지 않고 규칙 기반만 실행"
    )
    parser.add_argument(
        "--no-easy", action="store_true", help="easy negative를 넣지 않는다"
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=1.0,
        help="이 스크립트만 전역 설정(0)을 안 쓴다 — 상단 주석 참고",
    )
    args = parser.parse_args()

    items = ITEMS[: args.limit] if args.limit else ITEMS
    rng = random.Random(args.seed)

    structural = mine_structural_negatives(items)
    corpus = mine_corpus_negatives(items)
    print(
        f"규칙 기반 마이닝: 강화 형제 보유 아이템 {len(structural)}건 / "
        f"카테고리 내 유사 아이템 {len(corpus)}건"
    )

    if args.dry_run:
        llm_results = {item["item_id"]: (fallback_queries(item), []) for item in items}
        print("--dry-run: LLM 호출 없이 규칙 기반 트리플만 생성합니다.")
    else:
        # 전역 클라이언트(temperature=0)를 쓰지 않는다 — 상단 주석 참고.
        settings = get_settings()
        llm = OpenAIClient(
            api_key=settings.openai_api_key,
            model=settings.openai_model,
            temperature=args.temperature,
        )
        print(f"LLM temperature={args.temperature} (다양성 목적, 전역 설정 미사용)")
        semaphore = asyncio.Semaphore(args.concurrency)

        async def one(item: dict):
            async with semaphore:
                return item["item_id"], await generate_llm_pairs(
                    llm, item, args.queries_per_item, args.negatives_per_item
                )

        print(f"LLM 호출 {len(items)}건 (동시 {args.concurrency})...")
        llm_results = dict(await asyncio.gather(*(one(i) for i in items)))

    # 승격 후보는 학습용 아이템만. 평가 아이템 이름은 금지 목록으로 넘겨
    # 합성 negative에서도 배제한다 — 양방향으로 홀드아웃을 지킨다.
    items_by_name = {normalize_name(i["name"]): i for i in ITEMS}
    forbidden = {normalize_name(i["name"]) for i in EVAL_ITEMS}

    triplets = []
    empty = []
    total_dropped = 0
    total_promoted = 0
    for item in items:
        queries, synthetic = llm_results.get(item["item_id"], ([], []))
        if not queries:
            empty.append(item["item_id"])
            continue

        synthetic, promoted, dropped = sanitize_synthetic(item, synthetic, items_by_name, forbidden)
        total_dropped += dropped
        total_promoted += len(promoted)

        triplets.extend(
            build_triplets(
                item=item,
                queries=queries,
                synthetic_negatives=synthetic,
                structural=structural.get(item["item_id"], []),
                corpus=corpus.get(item["item_id"], []),
                easy=pick_easy_negative(item, items, rng),
                include_easy=not args.no_easy,
                promoted=promoted,
            )
        )

    if empty:
        print(f"경고: anchor를 못 만든 아이템 {len(empty)}건 (item_id={empty})")
    if total_dropped:
        print(f"검수: 자기 자신을 negative로 뱉은 합성 {total_dropped}건 폐기(false negative 방지)")
    if total_promoted:
        print(f"검수: 실재 아이템과 일치하는 합성 negative {total_promoted}건을 corpus로 승격")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with io.open(out_path, "w", encoding="utf-8", newline="\n") as f:
        for t in triplets:
            f.write(json.dumps(t.to_dict(), ensure_ascii=False) + "\n")

    report = quality_report(triplets)
    print(f"\n생성 완료: {out_path}  총 {report['total']}건")
    print(f"{'negative 유형':<12} {'건수':>6} {'이름유사도':>12} {'전체유사도':>12}")
    for kind, stats in report["by_negative_type"].items():
        print(
            f"{kind:<12} {stats['count']:>6} "
            f"{stats['avg_name_similarity']:>12.4f} {stats['avg_full_similarity']:>12.4f}"
        )

    easy_stats = report["by_negative_type"].get("easy")
    if not easy_stats:
        return

    # 각 hard 유형이 자기에게 공정한 척도에서 easy를 넘는지 본다.
    # synthetic은 이름만 있으므로 이름 척도로, 나머지는 전체 텍스트 척도로.
    fair_metric = {
        "synthetic": "avg_name_similarity",
        "structural": "avg_full_similarity",
        "corpus": "avg_full_similarity",
    }
    print()
    for kind, stats in report["by_negative_type"].items():
        if kind == "easy":
            continue
        metric = fair_metric.get(kind, "avg_name_similarity")
        if stats[metric] > easy_stats[metric]:
            print(f"[OK] {kind}: {stats[metric]:.4f} > easy {easy_stats[metric]:.4f} ({metric})")
        else:
            print(
                f"[경고] {kind}: {stats[metric]:.4f} <= easy {easy_stats[metric]:.4f} "
                f"({metric}) — 이 유형은 충분히 어렵지 않습니다."
            )


if __name__ == "__main__":
    asyncio.run(main())
