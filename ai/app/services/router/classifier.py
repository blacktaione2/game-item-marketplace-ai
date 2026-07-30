"""KoELECTRA 의도 분류기 (룰이 기권한 질의만 처리).

`monologg/koelectra-small-v3-discriminator`(14M)를 쓴다. base(110M)가 아닌
이유는 배포 대상이 4 OCPU 공유 인프라이고, 클래스 5개짜리 짧은 문장 분류에
110M을 태울 이유가 없기 때문이다. small이 룰 기준선을 못 이기면 그때 base를
검토한다.

다른 모델들과 마찬가지로 지연 로딩한다.
"""

from __future__ import annotations

import threading
from functools import lru_cache
from pathlib import Path

from app.core.config import get_settings
from app.services.router.intents import TRAINABLE_INTENTS, Intent

MAX_LENGTH = 64


class IntentClassifierNotTrainedError(RuntimeError):
    def __init__(self, model_dir: str) -> None:
        super().__init__(
            f"의도 분류 모델이 없습니다({model_dir}). "
            "`python -m scripts.train_intent_router` 로 먼저 학습하세요."
        )


class IntentClassifier:
    def __init__(self, model_dir: str) -> None:
        self._model_dir = model_dir
        self._model = None
        self._tokenizer = None
        self._lock = threading.Lock()

    def is_available(self) -> bool:
        return (Path(self._model_dir) / "config.json").exists()

    def _load(self):
        if self._model is None:
            with self._lock:
                if self._model is None:
                    if not self.is_available():
                        raise IntentClassifierNotTrainedError(self._model_dir)
                    from transformers import (
                        AutoModelForSequenceClassification,
                        AutoTokenizer,
                    )

                    self._tokenizer = AutoTokenizer.from_pretrained(self._model_dir)
                    self._model = AutoModelForSequenceClassification.from_pretrained(
                        self._model_dir
                    )
                    self._model.eval()
        return self._model, self._tokenizer

    def predict(self, text: str) -> tuple[Intent, float]:
        """(의도, 확신도). 확신도 판정은 호출자(router)가 한다."""
        import torch

        model, tokenizer = self._load()
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


@lru_cache
def get_intent_classifier() -> IntentClassifier:
    return IntentClassifier(get_settings().intent_model_dir)
