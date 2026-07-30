"""시세 예측 모델 (LSTM, 자체 학습).

Transformer 대신 LSTM을 쓴다. 시계열 Transformer는 데이터가 많을 때 이기는
구조인데 여기는 아이템당 120일 규모이고, 배포 대상도 4 OCPU 공유 인프라라
어텐션 비용이 과하다(ADR-0008).

아이템별 모델이 아니라 **전역 모델 하나**다. 아이템마다 따로 학습하면
데이터가 조각날 뿐 아니라, 이력이 없는 콜드스타트 아이템은 학습 자체가
불가능해진다. dataset.py의 비율 정규화 덕분에 가격대가 전혀 다른 아이템들을
한 모델로 묶을 수 있다.

출력은 horizon일치를 한 번에 내놓는 direct multi-step이다. 하루씩 재귀
예측하면 오차가 누적되는 데다 구현도 복잡해진다.
"""

from __future__ import annotations

import json
from pathlib import Path

import torch
from torch import nn

from app.services.forecast.dataset import FEATURE_DIM

_WEIGHTS_FILE = "model.pt"
_CONFIG_FILE = "config.json"


class PriceLSTM(nn.Module):
    def __init__(self, hidden_size: int = 64, horizon: int = 7) -> None:
        super().__init__()
        self.hidden_size = hidden_size
        self.horizon = horizon
        self.lstm = nn.LSTM(FEATURE_DIM, hidden_size, batch_first=True)
        self.head = nn.Linear(hidden_size, horizon)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        output, _ = self.lstm(x)
        # 마지막 타임스텝의 은닉 상태만 사용 — 윈도우 전체를 요약한 벡터다.
        return self.head(output[:, -1, :])


def save_model(model: PriceLSTM, directory: str, window: int) -> None:
    path = Path(directory)
    path.mkdir(parents=True, exist_ok=True)
    torch.save(model.state_dict(), path / _WEIGHTS_FILE)
    # 윈도우/호라이즌이 학습 때와 달라지면 추론이 조용히 틀리므로 같이 저장한다.
    (path / _CONFIG_FILE).write_text(
        json.dumps(
            {
                "hidden_size": model.hidden_size,
                "horizon": model.horizon,
                "window": window,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def load_model(directory: str) -> tuple[PriceLSTM, dict[str, int]]:
    path = Path(directory)
    config = json.loads((path / _CONFIG_FILE).read_text(encoding="utf-8"))
    model = PriceLSTM(hidden_size=config["hidden_size"], horizon=config["horizon"])
    model.load_state_dict(torch.load(path / _WEIGHTS_FILE, map_location="cpu"))
    model.eval()
    return model, config


def is_trained(directory: str) -> bool:
    path = Path(directory)
    return (path / _WEIGHTS_FILE).exists() and (path / _CONFIG_FILE).exists()
