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


def _quantization_config(auto_config):
    """int8 동적 양자화 설정. **아키텍처와 무관하게 avx2 다** (ADR-0032).

    이름이 x86 을 가리키지만 산출물은 이식 가능한 ONNX 이고, ARM 의 ONNX Runtime
    에서도 실행된다. `machine()` 을 읽지 않는다 — 읽었다가 되돌렸다.

    ## arm64 설정을 기각한 이유 (측정)

    같은 모델을 두 설정으로 양자화해 평가 질의 5건 × 후보 20건을 비교했다.

    | | 결과 |
    |---|---|
    | arm64 모델이 x86 에서 실행되는가 | **된다** (비교가 가능했다) |
    | top-1 일치 | **5/5** |
    | **top-5 순위 일치** | **0/5** |
    | 점수차 | 0.064 ~ 0.268 |

    2~5위가 매번 재배열된다. 그리고 **기록된 품질 수치는 전부 avx2 로 측정한
    것이다** — 하드 필터 부적합 50 → 14(ADR-0014·0015), 임베딩 Recall@1 등.
    양자화를 바꾸면 그 수치가 배포된 시스템을 더 이상 설명하지 않는다.

    바꿀 이유는 "ARM 에서 더 빠를 수 있다"인데 **그건 측정한 적이 없다.**
    재보지 않은 성능 이득을 위해 재본 품질 수치를 버리는 거래라서 기각했다.

    되살릴 조건: **ARM 에서 리랭킹이 실제로 병목으로 지목될 때.** 그때는
    `evaluate_hard_filters` 를 다시 돌려 품질 수치를 갱신하는 것이 전환의 일부다.
    """
    return auto_config.avx2(is_static=False, per_channel=False)


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
    # 동적 양자화(is_static=False): 캘리브레이션 데이터셋 없이 가중치만 int8로 낮춘다.
    #
    # 설정은 `_quantization_config` 이 정한다 — **아키텍처와 무관하게 avx2** 이고,
    # ARM 용 설정을 측정 후 기각한 근거가 그 함수 docstring 에 있다(ADR-0032).
    qconfig = _quantization_config(AutoQuantizationConfig)
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
