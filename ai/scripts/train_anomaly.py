"""이상거래 탐지 오토인코더 학습.

실행: python -m scripts.train_anomaly
      python -m scripts.train_anomaly --epochs 60 --percentile 99.5

정상 데이터를 학습 / 임계값 홀드아웃 / 평가용으로 셋으로 나눈다. 임계값은
반드시 **홀드아웃**에서 잡는다 — 학습셋 오차 분포로 잡으면 낙관 편향이
생겨 운영에서 알림이 예상보다 많이 터진다. 그 편향이 실제로 얼마나 되는지도
같이 출력한다.
"""

from __future__ import annotations

import argparse

import numpy as np
import torch
from torch import nn

from app.core.config import get_settings
from app.services.anomaly.dataset import build_splits
from app.services.anomaly.evaluation import (
    max_abs_z,
    pr_auc,
    price_zscore,
    recall_by_type,
)
from app.services.anomaly.model import TradeAutoencoder, save_model, squared_errors
from app.services.anomaly.scenarios import ANOMALY_LABELS

TENANT_CODE = "nexon"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--epochs", type=int, default=40)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--percentile", type=float, default=None, help="알림 임계 백분위수"
    )
    args = parser.parse_args()

    torch.manual_seed(args.seed)
    settings = get_settings()
    percentile = args.percentile or settings.anomaly_alert_percentile

    splits = build_splits()
    anomaly_count = int(splits.eval_labels.sum())
    print(
        f"정상 3분할 — 학습 {len(splits.train):,} / 임계값 홀드아웃 "
        f"{len(splits.threshold):,} / 평가 {len(splits.eval_x):,}"
    )
    print(
        f"평가셋 이상거래 {anomaly_count}건 "
        f"(기저율 {100 * anomaly_count / len(splits.eval_x):.2f}%)"
    )

    model = TradeAutoencoder()
    optimizer = torch.optim.Adam(model.parameters(), lr=args.lr)
    loss_fn = nn.MSELoss()
    x_tensor = torch.from_numpy(splits.train)

    for epoch in range(1, args.epochs + 1):
        model.train()
        permutation = torch.randperm(len(x_tensor))
        total = 0.0
        for start in range(0, len(permutation), args.batch_size):
            batch = x_tensor[permutation[start : start + args.batch_size]]
            optimizer.zero_grad()
            loss = loss_fn(model(batch), batch)
            loss.backward()
            optimizer.step()
            total += loss.item() * len(batch)
        if epoch % 10 == 0 or epoch == 1:
            print(f"  epoch {epoch:>3} | train MSE {total / len(x_tensor):.5f}")

    # --- 임계값: 학습셋으로 잡으면 얼마나 낙관적인지 같이 보여준다 --------
    train_scores = squared_errors(model, splits.train).sum(axis=1)
    holdout_scores = squared_errors(model, splits.threshold).sum(axis=1)
    train_threshold = float(np.percentile(train_scores, percentile))
    holdout_threshold = float(np.percentile(holdout_scores, percentile))

    print(f"\n임계값 산정 (p{percentile})")
    print(f"  학습셋 기준     {train_threshold:.4f}  <- 쓰면 안 되는 값")
    print(f"  홀드아웃 기준   {holdout_threshold:.4f}  <- 채택")
    leak_rate = float((holdout_scores > train_threshold).mean()) * 100
    print(
        f"  학습셋 임계값을 홀드아웃에 적용하면 정상의 {leak_rate:.2f}%가 알림 "
        f"(의도한 {100 - percentile:.2f}%)"
    )

    # --- 평가: 오토인코더 vs 규칙 기준선 ---------------------------------
    eval_scores = {
        "오토인코더": squared_errors(model, splits.eval_x).sum(axis=1),
        "규칙: 가격 |z|": price_zscore(splits.eval_x),
        "규칙: 전 피처 max |z|": max_abs_z(splits.eval_x),
    }
    # 알림 예산은 이상거래 수와 같게 잡는다(R-precision). 임의의 백분위수를
    # 고르면 그 선택이 결과를 좌우하고, 예산이 이상치 수보다 작으면 재현율에
    # 인위적 상한이 생겨 유형 간 비교가 성립하지 않는다.
    budget = anomaly_count

    print(f"\n평가 (알림 예산 = 이상거래 수 {budget}건, R-precision)")
    print(f"{'방법':<22}{'PR-AUC':>9}{'정밀도':>9}")
    per_type: dict[str, dict[str, tuple[int, int]]] = {}
    for name, scores in eval_scores.items():
        caught, precision = recall_by_type(
            splits.eval_labels, scores, splits.eval_types, budget
        )
        per_type[name] = caught
        print(
            f"{name:<22}{pr_auc(splits.eval_labels, scores):>9.3f}{precision:>9.3f}"
        )

    print("\n유형별 재현율 (적발/전체)")
    header = "".join(f"{name:>22}" for name in eval_scores)
    print(f"{'유형':<20}{header}")
    for anomaly_type, label in ANOMALY_LABELS.items():
        cells = ""
        for name in eval_scores:
            caught, total = per_type[name].get(anomaly_type, (0, 0))
            ratio = f"{caught}/{total}" if total else "-"
            share = f" ({caught / total:.0%})" if total else ""
            cells += f"{ratio + share:>22}"
        print(f"{label:<20}{cells}")

    # 실제 배포 임계값(홀드아웃 p99)을 평가 구간에 적용하면 어떻게 되는가.
    # 위의 R-precision은 방법 비교용이고, 이쪽이 운영에서 보게 될 숫자다.
    deployed = eval_scores["오토인코더"] > holdout_threshold
    hits = int((deployed & (splits.eval_labels == 1)).sum())
    print(
        f"\n배포 임계값(홀드아웃 p{percentile}) 적용 시: 알림 {int(deployed.sum())}건 중 "
        f"실제 이상 {hits}건 (정밀도 {hits / max(int(deployed.sum()), 1):.2f}, "
        f"재현율 {hits / max(anomaly_count, 1):.2f})"
    )

    save_model(
        model,
        settings.anomaly_model_dir,
        scaler_payload=splits.scaler.to_dict(),
        thresholds={TENANT_CODE: holdout_threshold},
        alert_percentile=percentile,
    )
    print(f"\n모델 저장: {settings.anomaly_model_dir}")


if __name__ == "__main__":
    main()
