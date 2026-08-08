"""KoELECTRA 의도 분류기 학습 + 룰 기준선 비교.

실행: python -m scripts.train_intent_router
      python -m scripts.train_intent_router --epochs 8

## 비교를 셋으로 하는 이유

배포되는 라우터는 "룰 우선, 기권하면 분류기"인 2단 구조다. 그런데 분류기
단독 정확도만 보고하면 **룰이 이미 하던 일을 분류기 공으로 돌리게 된다.**
그래서 셋을 같이 잰다.

1. 룰 단독      — 기준선. 이걸 못 이기면 분류기는 값을 못 한다
2. 분류기 단독  — 모델 자체의 능력
3. 2단 라우터   — 실제 배포되는 것

평가셋은 손으로 쓴 홀드아웃이다. 학습 데이터(LLM 생성)와 겹치지 않는 건
`app/corpus/intent_utterances.py`가 임포트 시점에 검사한다.

**룰은 평가셋 오답을 보고 고치지 않았다.** 고치면 기준선이 부풀고 비교가
오염된다.
"""

from __future__ import annotations

import argparse
from collections import Counter

import torch
from torch.utils.data import DataLoader, TensorDataset

from app.corpus.intent_utterances import (
    BOUNDARY_UTTERANCES,
    EVAL_UTTERANCES,
    load_train_utterances,
)
from app.core.config import get_settings
from app.services.router.classifier import MAX_LENGTH
from app.services.router.intents import INTENT_LABELS, TRAINABLE_INTENTS, Intent
from app.services.router.rules import classify_by_rules

_INDEX = {intent: i for i, intent in enumerate(TRAINABLE_INTENTS)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--lr", type=float, default=5e-5)
    # 레이블 스무딩 — 확신도 과확신을 줄이기 위한 것이다. 스무딩 없이 손실을
    # 0.01까지 떨어뜨렸더니 오답에도 0.98을 뱉어서 확신도 임계값이 무의미해졌다.
    parser.add_argument("--label-smoothing", type=float, default=0.1)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--val-ratio",
        type=float,
        default=0.1,
        help="수렴 확인용. 학습 데이터에서 떼며, 수작업 평가셋은 건드리지 않는다",
    )
    args = parser.parse_args()

    torch.manual_seed(args.seed)
    settings = get_settings()

    train_rows = load_train_utterances()
    if not train_rows:
        raise SystemExit(
            "학습 발화가 없습니다. `python -m scripts.generate_intent_data` 를 먼저 실행하세요."
        )

    distribution = Counter(intent.value for _, intent in train_rows)
    print(f"학습 발화 {len(train_rows)}건 {dict(distribution)}")
    print(f"평가 발화 {len(EVAL_UTTERANCES)}건 (수작업 홀드아웃)")

    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(settings.intent_base_model)
    model = AutoModelForSequenceClassification.from_pretrained(
        settings.intent_base_model, num_labels=len(TRAINABLE_INTENTS)
    )

    # 수렴 확인용 분할. **수작업 평가셋은 여기에 절대 쓰지 않는다** — 에폭 수를
    # 평가셋 보고 정하면 그 순간 평가셋이 튜닝 데이터가 된다.
    generator = torch.Generator().manual_seed(args.seed)
    order = torch.randperm(len(train_rows), generator=generator).tolist()
    split = int(len(order) * (1 - args.val_ratio))
    fit_rows = [train_rows[i] for i in order[:split]]
    val_rows = [train_rows[i] for i in order[split:]]

    def encode(rows):
        batch = tokenizer(
            [text for text, _ in rows],
            truncation=True,
            max_length=MAX_LENGTH,
            padding="max_length",
            return_tensors="pt",
        )
        return (
            batch["input_ids"],
            batch["attention_mask"],
            torch.tensor([_INDEX[intent] for _, intent in rows]),
        )

    fit_tensors = encode(fit_rows)
    val_ids, val_mask, val_labels = encode(val_rows)
    loader = DataLoader(
        TensorDataset(*fit_tensors), batch_size=args.batch_size, shuffle=True
    )

    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr)
    for epoch in range(1, args.epochs + 1):
        model.train()
        total = 0.0
        for input_ids, attention_mask, target in loader:
            optimizer.zero_grad()
            logits = model(input_ids=input_ids, attention_mask=attention_mask).logits
            # HF의 내장 loss는 스무딩을 안 걸어주므로 직접 계산한다.
            loss = torch.nn.functional.cross_entropy(
                logits, target, label_smoothing=args.label_smoothing
            )
            loss.backward()
            optimizer.step()
            total += loss.item() * len(target)

        if epoch % 5 == 0 or epoch == 1:
            model.eval()
            with torch.no_grad():
                predicted = model(
                    input_ids=val_ids, attention_mask=val_mask
                ).logits.argmax(dim=-1)
            accuracy = (predicted == val_labels).float().mean().item()
            print(
                f"  epoch {epoch:>3} | loss {total / len(fit_rows):.4f} "
                f"| val(LLM셋) {accuracy:.1%}"
            )

    model.eval()
    model.save_pretrained(settings.intent_model_dir)
    tokenizer.save_pretrained(settings.intent_model_dir)
    print(f"\n모델 저장: {settings.intent_model_dir}")

    _report(model, tokenizer, settings.intent_confidence_threshold)


def _predict(model, tokenizer, text: str) -> tuple[Intent, float]:
    encoded = tokenizer(
        text,
        return_tensors="pt",
        truncation=True,
        max_length=MAX_LENGTH,
        padding=True,
    )
    with torch.no_grad():
        probabilities = torch.softmax(model(**encoded).logits, dim=-1)[0]
    index = int(probabilities.argmax())
    return TRAINABLE_INTENTS[index], float(probabilities[index])


def _report(model, tokenizer, threshold: float) -> None:
    total = len(EVAL_UTTERANCES)
    rules_ok = classifier_ok = router_ok = 0
    rules_abstained = 0
    per_class_router: Counter[Intent] = Counter()
    per_class_total: Counter[Intent] = Counter()
    router_errors: list[tuple[str, Intent, Intent, str]] = []

    for text, gold in EVAL_UTTERANCES:
        per_class_total[gold] += 1

        rule_intent = classify_by_rules(text)
        if rule_intent is None:
            rules_abstained += 1
        elif rule_intent == gold:
            rules_ok += 1

        predicted, confidence = _predict(model, tokenizer, text)
        if predicted == gold:
            classifier_ok += 1

        # 실제 배포되는 2단 라우터
        if rule_intent is not None:
            final, decided = rule_intent, "rules"
        elif confidence >= threshold:
            final, decided = predicted, "classifier"
        else:
            final, decided = Intent.COMPOUND, "low_confidence"

        if final == gold:
            router_ok += 1
            per_class_router[gold] += 1
        else:
            router_errors.append((text, gold, final, decided))

    print(f"\n{'=' * 62}\n홀드아웃 정확도 ({total}건, 전부 수작업)\n{'=' * 62}")
    print(f"{'방법':<28}{'정확도':>10}")
    print(f"{'룰 단독 (기준선)':<28}{rules_ok / total:>9.1%}")
    print(f"{'분류기 단독':<28}{classifier_ok / total:>9.1%}")
    print(f"{'2단 라우터 (배포)':<28}{router_ok / total:>9.1%}")
    print(f"\n룰 기권 {rules_abstained}건 - 분류기가 담당하는 구간")

    if router_ok > rules_ok:
        print(f"=> 2단 라우터가 룰 기준선을 {router_ok - rules_ok}건 앞선다")
    else:
        print("=> 룰 기준선을 못 이겼다. 분류기를 넣을 근거가 없다.")

    print("\n2단 라우터 클래스별 정확도")
    for intent in TRAINABLE_INTENTS:
        count = per_class_total[intent]
        if count:
            print(
                f"  {INTENT_LABELS[intent]:<14} "
                f"{per_class_router[intent]}/{count}"
            )

    if router_errors:
        print("\n2단 라우터 오답")
        for text, gold, predicted, decided in router_errors:
            print(
                f'  "{text}" 정답={INTENT_LABELS[gold]} '
                f"예측={INTENT_LABELS[predicted]} ({decided})"
            )

    # --- 경계 발화: 맞히는 게 아니라 에이전트로 빠지는 게 목표 -------------
    escalated = 0
    breakdown: Counter[str] = Counter()
    for text in BOUNDARY_UTTERANCES:
        rule_intent = classify_by_rules(text)
        if rule_intent is not None:
            final, decided = rule_intent, "rules"
        else:
            predicted, confidence = _predict(model, tokenizer, text)
            if confidence >= threshold:
                final, decided = predicted, "classifier"
            else:
                final, decided = Intent.COMPOUND, "low_confidence"
        if final == Intent.COMPOUND:
            escalated += 1
        breakdown[f"{INTENT_LABELS[final]} ({decided})"] += 1

    print(f"\n{'=' * 62}\n경계 발화 {len(BOUNDARY_UTTERANCES)}건 - 에이전트로 빠져야 정상")
    print(f"{'=' * 62}")
    print(f"COMPOUND로 에스컬레이션: {escalated}/{len(BOUNDARY_UTTERANCES)}")
    for key, value in breakdown.most_common():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
