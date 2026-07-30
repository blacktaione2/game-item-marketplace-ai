"""시세 예측 LSTM 학습.

실행: python -m scripts.train_forecast
      python -m scripts.train_forecast --epochs 60 --hidden 96

학습만 하고 끝내지 않고 **나이브 기준선과 같이 측정한다**. 시세 예측에서
직전값 유지(random walk)는 이기기 어려운 기준선이고, 더미 데이터는 패턴을
알고 만든 합성 데이터라 모델 MAPE 단독으로는 아무것도 증명하지 못한다.
기준선을 못 이기면 그 사실을 그대로 출력한다.
"""

from __future__ import annotations

import argparse

import numpy as np
import torch
from torch import nn

from app.corpus.trade_history import HISTORY_SPECS, get_price_series
from app.core.config import get_settings
from app.services.forecast.dataset import build_dataset
from app.services.forecast.evaluation import (
    mape,
    naive_drift,
    naive_last,
    signal_correlation,
)
from app.services.forecast.model import PriceLSTM, save_model


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--epochs", type=int, default=40)
    parser.add_argument("--hidden", type=int, default=64)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    torch.manual_seed(args.seed)
    settings = get_settings()
    window = settings.forecast_window
    horizon = settings.forecast_horizon

    series_by_item = {
        item_id: get_price_series(item_id) for item_id in sorted(HISTORY_SPECS)
    }
    train_x, train_y, val_x, val_y = build_dataset(series_by_item, window, horizon)
    print(
        f"아이템 {len(series_by_item)}건 / 학습 윈도우 {len(train_x)}개, "
        f"검증 윈도우 {len(val_x)}개 (아이템별 시간순 분할)"
    )

    model = PriceLSTM(hidden_size=args.hidden, horizon=horizon)
    optimizer = torch.optim.Adam(model.parameters(), lr=args.lr)
    loss_fn = nn.MSELoss()

    x_tensor = torch.from_numpy(train_x)
    y_tensor = torch.from_numpy(train_y)
    val_x_tensor = torch.from_numpy(val_x)

    for epoch in range(1, args.epochs + 1):
        model.train()
        permutation = torch.randperm(len(x_tensor))
        total = 0.0
        for start in range(0, len(permutation), args.batch_size):
            batch_idx = permutation[start : start + args.batch_size]
            optimizer.zero_grad()
            loss = loss_fn(model(x_tensor[batch_idx]), y_tensor[batch_idx])
            loss.backward()
            optimizer.step()
            total += loss.item() * len(batch_idx)

        if epoch % 10 == 0 or epoch == 1:
            model.eval()
            with torch.no_grad():
                val_pred = model(val_x_tensor).numpy()
            print(
                f"  epoch {epoch:>3} | train MSE {total / len(x_tensor):.6f} "
                f"| val MAPE {mape(val_y, val_pred):.2f}%"
            )

    model.eval()
    with torch.no_grad():
        val_pred = model(val_x_tensor).numpy()

    scores = {
        "LSTM": mape(val_y, val_pred),
        "나이브(직전값 유지)": mape(val_y, naive_last(val_x, horizon)),
        "나이브(선형 외삽)": mape(val_y, naive_drift(val_x, horizon)),
    }

    print("\n검증셋 MAPE (낮을수록 좋음)")
    for name, score in scores.items():
        print(f"  {name:<16} {score:.2f}%")

    best_naive = min(scores["나이브(직전값 유지)"], scores["나이브(선형 외삽)"])
    if scores["LSTM"] < best_naive:
        gain = (1 - scores["LSTM"] / best_naive) * 100
        print(f"\n=> LSTM이 최선 기준선 대비 오차 {gain:.1f}% 감소")
    else:
        print("\n=> LSTM이 기준선을 이기지 못했습니다. 모델/데이터를 재검토하세요.")

    # MAPE 개선폭이 작을 때 "모델이 그냥 1.0을 뱉는 것 아닌가"를 가려내는 지표.
    correlation = signal_correlation(val_y, val_pred)
    print(
        f"   신호 상관 {correlation:.3f} "
        f"(예측 편차 std {val_pred.std():.4f} vs 실제 {val_y.std():.4f})"
    )
    if correlation < 0.1:
        print("   ! 상관이 0에 가깝습니다 — 모델이 상수로 붕괴했을 가능성이 큽니다.")

    print("\n예측 시점(horizon)별 MAPE")
    for step in range(horizon):
        print(
            f"  D+{step + 1}  LSTM {mape(val_y[:, step], val_pred[:, step]):>6.2f}%  "
            f"| 직전값 {mape(val_y[:, step], np.ones(len(val_y))):>6.2f}%"
        )

    save_model(model, settings.forecast_model_dir, window=window)
    print(f"\n모델 저장: {settings.forecast_model_dir}")


if __name__ == "__main__":
    main()
