"""Cross-Encoder 리랭커 (ONNX + int8 동적 양자화).

레이턴시 예산 때문에 PyTorch 원본 대신 ONNX Runtime + int8 양자화 모델을
쓰고, 리랭킹 대상도 상위 후보(기본 20건)로 제한한다. 크로스인코더는
(질의, 문서) 쌍마다 forward를 돌리므로 후보 수에 레이턴시가 그대로 비례한다.

모델은 최초 사용 시점에 지연 로딩하고, 변환/양자화 산출물은 디스크에
캐시해서 재기동 때 재사용한다.
"""

from __future__ import annotations

import logging
import threading
from functools import lru_cache
from pathlib import Path

from app.core.config import get_settings

logger = logging.getLogger(__name__)


def build_quantized_model(model_id: str, output_dir: Path) -> None:
    """HF 모델을 ONNX로 변환하고 int8 동적 양자화해서 output_dir에 저장."""
    from optimum.onnxruntime import ORTModelForSequenceClassification, ORTQuantizer
    from optimum.onnxruntime.configuration import AutoQuantizationConfig
    from transformers import AutoTokenizer

    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info("크로스인코더 ONNX 변환 시작: %s", model_id)
    model = ORTModelForSequenceClassification.from_pretrained(model_id, export=True)
    tokenizer = AutoTokenizer.from_pretrained(model_id)

    quantizer = ORTQuantizer.from_pretrained(model)
    # 동적 양자화(is_static=False): 캘리브레이션 데이터셋 없이 가중치만 int8로
    # 낮춘다. avx2 설정은 x86 개발 환경 기준 — 배포 대상인 Oracle Cloud ARM에
    # 올릴 때는 AutoQuantizationConfig.arm64로 다시 생성하는 편이 좋다.
    qconfig = AutoQuantizationConfig.avx2(is_static=False, per_channel=False)
    quantizer.quantize(save_dir=output_dir, quantization_config=qconfig)
    tokenizer.save_pretrained(output_dir)
    logger.info("크로스인코더 int8 양자화 완료: %s", output_dir)


class RerankerService:
    def __init__(self, model_id: str, onnx_dir: str, max_candidates: int) -> None:
        self._model_id = model_id
        self._onnx_dir = Path(onnx_dir)
        self._max_candidates = max_candidates
        self._model = None
        self._tokenizer = None
        self._lock = threading.Lock()

    def _load(self):
        if self._model is None:
            with self._lock:
                if self._model is None:
                    from optimum.onnxruntime import ORTModelForSequenceClassification
                    from transformers import AutoTokenizer

                    if not (self._onnx_dir / "config.json").exists():
                        build_quantized_model(self._model_id, self._onnx_dir)

                    self._model = ORTModelForSequenceClassification.from_pretrained(
                        self._onnx_dir, file_name="model_quantized.onnx"
                    )
                    self._tokenizer = AutoTokenizer.from_pretrained(self._onnx_dir)
        return self._model, self._tokenizer

    def rerank(
        self, query: str, documents: list[dict], text_key: str = "name"
    ) -> list[dict]:
        """상위 max_candidates건만 크로스인코더로 재정렬하고 나머지는 뒤에 붙인다."""
        if not documents:
            return documents

        head = documents[: self._max_candidates]
        tail = documents[self._max_candidates :]

        model, tokenizer = self._load()
        pairs = [(query, _document_text(doc, text_key)) for doc in head]

        inputs = tokenizer(
            [p[0] for p in pairs],
            [p[1] for p in pairs],
            padding=True,
            truncation=True,
            max_length=256,
            return_tensors="pt",
        )
        outputs = model(**inputs)
        scores = outputs.logits.squeeze(-1).tolist()
        if isinstance(scores, float):
            scores = [scores]

        for doc, score in zip(head, scores):
            doc["rerank_score"] = float(score)

        head.sort(key=lambda d: d["rerank_score"], reverse=True)
        return [*head, *tail]


def _document_text(doc: dict, text_key: str) -> str:
    name = doc.get(text_key) or ""
    description = doc.get("description") or ""
    return f"{name} {description}".strip()


@lru_cache
def get_reranker() -> RerankerService:
    settings = get_settings()
    return RerankerService(
        model_id=settings.reranker_model,
        onnx_dir=settings.reranker_onnx_dir,
        max_candidates=settings.rerank_candidates,
    )
