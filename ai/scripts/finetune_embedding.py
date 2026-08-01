"""MultipleNegativesRankingLoss로 임베딩 모델을 파인튜닝한다.

학습 데이터는 TRAIN_ITEMS로 만든 트리플뿐이다. EVAL_ITEMS는 학습에 전혀
들어가지 않는다.

**easy negative를 학습에서 빼는 이유**: MNRL은 배치 안의 다른 positive들을
자동으로 negative로 쓴다(in-batch negatives). 그 배치 내 무작위 negative가
이미 easy negative 역할을 하므로, 명시적 negative 슬롯에는 hard한 것만 넣는
게 학습 신호가 강하다.

실행: python -m scripts.finetune_embedding
      python -m scripts.finetune_embedding --epochs 2 --batch-size 16
"""

from __future__ import annotations

import argparse
import io
import json
from pathlib import Path

from app.core.config import get_settings

HARD_TYPES = {"structural", "corpus", "synthetic"}


def load_triplets(path: Path, include_easy: bool) -> list[dict]:
    rows = [json.loads(line) for line in io.open(path, encoding="utf-8")]
    if not include_easy:
        rows = [r for r in rows if r["negative_type"] in HARD_TYPES]
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--triplets", default="data/train_triplets.jsonl")
    parser.add_argument("--out", default="models/embedding-finetuned")
    parser.add_argument("--epochs", type=int, default=2)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--lr", type=float, default=2e-5)
    parser.add_argument("--warmup-ratio", type=float, default=0.1)
    parser.add_argument("--include-easy", action="store_true")
    args = parser.parse_args()

    from datasets import Dataset
    from sentence_transformers import (
        SentenceTransformer,
        SentenceTransformerTrainer,
        SentenceTransformerTrainingArguments,
    )
    from sentence_transformers.losses import MultipleNegativesRankingLoss

    settings = get_settings()
    rows = load_triplets(Path(args.triplets), args.include_easy)
    print(f"학습 트리플 {len(rows)}건 (easy 포함={args.include_easy})")

    # MNRL은 컬럼 순서로 (anchor, positive, negative)를 읽는다.
    dataset = Dataset.from_dict(
        {
            "anchor": [r["anchor"] for r in rows],
            "positive": [r["positive"] for r in rows],
            "negative": [r["negative"] for r in rows],
        }
    )

    # 베이스는 스톡 모델이다. `settings.embedding_model` 은 이 스크립트의
    # **출력 경로**라서, 그걸 쓰면 자기 출력물을 파인튜닝하려 든다(새 환경에서는
    # 디렉터리가 없어 HuggingFace 저장소 id로 해석돼 401로 죽는다).
    model = SentenceTransformer(settings.embedding_base_model)
    loss = MultipleNegativesRankingLoss(model)

    out_dir = Path(args.out)
    training_args = SentenceTransformerTrainingArguments(
        output_dir=str(out_dir / "checkpoints"),
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        learning_rate=args.lr,
        warmup_ratio=args.warmup_ratio,
        logging_steps=10,
        save_strategy="no",
        report_to=[],
    )

    trainer = SentenceTransformerTrainer(
        model=model,
        args=training_args,
        train_dataset=dataset,
        loss=loss,
    )
    trainer.train()

    out_dir.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(str(out_dir))
    print(f"\n저장 완료: {out_dir}")
    print("평가: python -m scripts.evaluate_embedding")


if __name__ == "__main__":
    main()
