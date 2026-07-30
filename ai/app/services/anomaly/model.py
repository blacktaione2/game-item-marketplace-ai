"""이상거래 탐지 오토인코더.

Isolation Forest가 아니라 오토인코더를 쓰는 이유는 **설명가능성** 하나다
(계획서 명시). Isolation Forest는 "이상함"까지만 말하지만, 오토인코더는
피처별 재구성 오차를 쪼갤 수 있어 "무엇이 이상한지"를 말할 수 있다. GM이
검토 큐에서 바로 판단하려면 후자가 필요하다.

구조는 11 → 8 → 4 → 8 → 11. 병목 4차원은 압축을 강제할 만큼 좁고 정상 거래의
다양성은 담을 만한 선이다. 병목이 넓으면 항등함수를 학습해버려서 이상치까지
잘 재구성하게 된다.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import torch
from torch import nn

from app.services.anomaly.features import FEATURE_DIM

_WEIGHTS_FILE = "model.pt"
_CONFIG_FILE = "config.json"


class TradeAutoencoder(nn.Module):
    def __init__(self, hidden_size: int = 8, bottleneck: int = 4) -> None:
        super().__init__()
        self.hidden_size = hidden_size
        self.bottleneck = bottleneck
        self.encoder = nn.Sequential(
            nn.Linear(FEATURE_DIM, hidden_size),
            nn.ReLU(),
            nn.Linear(hidden_size, bottleneck),
        )
        self.decoder = nn.Sequential(
            nn.Linear(bottleneck, hidden_size),
            nn.ReLU(),
            # 입력이 정규화된 실수라 출력은 선형으로 둔다.
            nn.Linear(hidden_size, FEATURE_DIM),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.decoder(self.encoder(x))


def squared_errors(model: TradeAutoencoder, x: np.ndarray) -> np.ndarray:
    """(n, FEATURE_DIM) 피처별 제곱오차. 합치면 이상 점수, 쪼개면 기여도."""
    model.eval()
    with torch.no_grad():
        tensor = torch.from_numpy(x)
        reconstructed = model(tensor)
        return ((tensor - reconstructed) ** 2).numpy()


def save_model(
    model: TradeAutoencoder,
    directory: str,
    scaler_payload: dict[str, list[float]],
    thresholds: dict[str, float],
    alert_percentile: float,
) -> None:
    path = Path(directory)
    path.mkdir(parents=True, exist_ok=True)
    torch.save(model.state_dict(), path / _WEIGHTS_FILE)
    (path / _CONFIG_FILE).write_text(
        json.dumps(
            {
                "hidden_size": model.hidden_size,
                "bottleneck": model.bottleneck,
                "scaler": scaler_payload,
                # 테넌트별 임계값. 게임사마다 거래 분포가 다르므로 하나의
                # 전역 값을 공유하면 어느 한쪽은 반드시 어긋난다.
                "thresholds": thresholds,
                "alert_percentile": alert_percentile,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def load_model(directory: str) -> tuple[TradeAutoencoder, dict]:
    path = Path(directory)
    config = json.loads((path / _CONFIG_FILE).read_text(encoding="utf-8"))
    model = TradeAutoencoder(
        hidden_size=config["hidden_size"], bottleneck=config["bottleneck"]
    )
    model.load_state_dict(torch.load(path / _WEIGHTS_FILE, map_location="cpu"))
    model.eval()
    return model, config


def is_trained(directory: str) -> bool:
    path = Path(directory)
    return (path / _WEIGHTS_FILE).exists() and (path / _CONFIG_FILE).exists()
