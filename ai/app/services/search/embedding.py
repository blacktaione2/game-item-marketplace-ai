"""임베딩 모델 래퍼.

배포 대상이 공유 인프라(4 OCPU / 24GB)라서 모델은 상주시키지 않고 최초
사용 시점에 지연 로딩한다(계획서의 lazy loading 제약).
"""

from __future__ import annotations

import threading
from functools import lru_cache

from app.core.config import get_settings


class EmbeddingService:
    def __init__(self, model_name: str) -> None:
        self._model_name = model_name
        self._model = None
        self._lock = threading.Lock()

    def _get_model(self):
        # 첫 호출에만 로딩. 동시 요청이 겹쳐도 한 번만 로딩되도록 락을 건다.
        if self._model is None:
            with self._lock:
                if self._model is None:
                    from sentence_transformers import SentenceTransformer

                    self._model = SentenceTransformer(self._model_name)
        return self._model

    def encode(self, texts: list[str]) -> list[list[float]]:
        model = self._get_model()
        vectors = model.encode(texts, normalize_embeddings=True)
        return [v.tolist() for v in vectors]

    def encode_one(self, text: str) -> list[float]:
        return self.encode([text])[0]


@lru_cache
def get_embedding_service() -> EmbeddingService:
    return EmbeddingService(get_settings().embedding_model)
