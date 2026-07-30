"""임베딩 검색 품질 평가.

**왜 dense 검색만 재는가**

파인튜닝이 바꾸는 건 임베딩뿐이다. 전체 하이브리드 파이프라인(BM25 + kNN +
RRF + 리랭킹)으로 재면 변하지 않은 BM25와 리랭커가 신호를 희석시켜서, 임베딩이
좋아졌는지 나빠졌는지를 알 수 없게 된다. 그래서 여기서는 **질의 임베딩과 아이템
임베딩의 코사인 랭킹만** 측정한다.

실서비스 체감 성능과는 다른 수치라는 점을 유의할 것 — 이 값은 "임베딩 자체가
좋아졌는가"만 답한다.

**왜 RAGAS 말고 IR 지표도 재는가**

RAGAS 지표는 LLM 심판이라 실행마다 흔들리고, 평가셋이 작으면 그 분산이
개선폭보다 클 수 있다. Recall@k / MRR은 결정론적이라 기준점 역할을 한다.
둘이 어긋나면 그 자체가 중요한 정보다.
"""

from __future__ import annotations

from typing import Any

import numpy as np


def encode_corpus(model, texts: list[str]) -> np.ndarray:
    return np.asarray(model.encode(texts, normalize_embeddings=True))


def rank_all(
    model, queries: list[str], corpus_vectors: np.ndarray
) -> np.ndarray:
    """각 질의에 대해 코퍼스 인덱스를 유사도 내림차순으로 정렬해 반환."""
    query_vectors = np.asarray(model.encode(queries, normalize_embeddings=True))
    # 둘 다 정규화돼 있으므로 내적이 곧 코사인 유사도.
    scores = query_vectors @ corpus_vectors.T
    return np.argsort(-scores, axis=1)


def ir_metrics(
    rankings: np.ndarray, gold_indices: list[int], ks: tuple[int, ...] = (1, 3, 5, 10)
) -> dict[str, float]:
    """Recall@k와 MRR. 정답이 아이템 하나뿐이므로 Recall@k = Hit@k와 같다."""
    n = len(gold_indices)
    result: dict[str, float] = {}

    # 각 질의에서 정답이 몇 등인지 (0-based)
    positions = []
    for row, gold in zip(rankings, gold_indices):
        found = np.where(row == gold)[0]
        positions.append(int(found[0]) if len(found) else len(row))

    for k in ks:
        result[f"recall@{k}"] = round(sum(p < k for p in positions) / n, 4)

    result["mrr"] = round(sum(1.0 / (p + 1) for p in positions) / n, 4)
    result["mean_rank"] = round(sum(p + 1 for p in positions) / n, 2)
    return result


def evaluate_model(
    model,
    queries: list[str],
    gold_indices: list[int],
    corpus_texts: list[str],
) -> tuple[dict[str, float], np.ndarray]:
    corpus_vectors = encode_corpus(model, corpus_texts)
    rankings = rank_all(model, queries, corpus_vectors)
    return ir_metrics(rankings, gold_indices), rankings


def compare(before: dict[str, float], after: dict[str, float]) -> list[dict[str, Any]]:
    rows = []
    for key in before:
        delta = after[key] - before[key]
        # mean_rank는 낮을수록 좋고 나머지는 높을수록 좋다.
        improved = delta < 0 if key == "mean_rank" else delta > 0
        rows.append(
            {
                "metric": key,
                "before": before[key],
                "after": after[key],
                "delta": round(delta, 4),
                "improved": improved if abs(delta) > 1e-9 else None,
            }
        )
    return rows
