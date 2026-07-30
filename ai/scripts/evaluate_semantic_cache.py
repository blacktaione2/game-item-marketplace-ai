"""시맨틱 캐시 임계값 산정.

실행: python -m scripts.evaluate_semantic_cache

임계값을 훑으며 **적중률(동의 쌍이 히트)과 오탐율(함정 쌍이 히트)을 같이**
잰다. 적중률만 보면 임계값을 낮출수록 좋아 보이지만, 그때 다른 질문에 남의
답을 돌려주기 시작한다.

## 기준선: 정확 일치 캐시

문자열이 완전히 같을 때만 히트하는 캐시가 기준선이다. 오탐 0%는 당연히
보장되므로, 시맨틱 캐시는 **오탐을 감당 가능한 수준으로 유지하면서 적중률을
올려야** 존재 이유가 있다. 못 올리면 복잡도만 늘린 것이다.

## 임계값은 튜닝 쌍에서 정하고 홀드아웃으로 확인한다

정한 데이터로 성능을 보고하면 낙관 편향이 생긴다 — Phase 5-2에서 실측한
그 문제다.
"""

from __future__ import annotations

import numpy as np

from app.corpus.cache_pairs import holdout_pairs, tuning_pairs
from app.services.search.embedding import get_embedding_service

THRESHOLDS = [0.80, 0.85, 0.88, 0.90, 0.92, 0.94, 0.95, 0.96, 0.97, 0.98, 0.99]
# 오탐은 조용히 잘못된 답을 내보내므로 적중률보다 훨씬 비싸다. 튜닝 쌍에서
# 이 상한을 넘지 않는 것 중 적중률이 가장 높은 임계값을 고른다.
MAX_FALSE_HIT_RATE = 0.0


def similarities(pairs: list[tuple[str, str]]) -> np.ndarray:
    embedder = get_embedding_service()
    left = np.array(embedder.encode([a for a, _ in pairs]), dtype=np.float32)
    right = np.array(embedder.encode([b for _, b in pairs]), dtype=np.float32)
    # encode()가 이미 정규화해서 내주므로 내적이 곧 코사인이다.
    return np.sum(left * right, axis=1)


def main() -> None:
    synonym_tuning, trap_tuning = tuning_pairs()
    synonym_holdout, trap_holdout = holdout_pairs()

    tuning_hit = similarities(synonym_tuning)
    tuning_trap = similarities(trap_tuning)
    holdout_hit = similarities(synonym_holdout)
    holdout_trap = similarities(trap_holdout)

    print(
        f"튜닝 쌍: 동의 {len(synonym_tuning)} / 함정 {len(trap_tuning)}    "
        f"홀드아웃: 동의 {len(synonym_holdout)} / 함정 {len(trap_holdout)}"
    )
    print(
        f"\n동의 쌍 유사도  중앙 {np.median(tuning_hit):.4f} "
        f"최소 {tuning_hit.min():.4f}"
    )
    print(
        f"함정 쌍 유사도  중앙 {np.median(tuning_trap):.4f} "
        f"최대 {tuning_trap.max():.4f}  <- 이 값을 넘겨야 오탐 0"
    )

    print(f"\n{'임계값':>8}{'적중률':>10}{'오탐율':>10}   (튜닝 쌍)")
    best = None
    for threshold in THRESHOLDS:
        hit_rate = float((tuning_hit >= threshold).mean())
        false_rate = float((tuning_trap >= threshold).mean())
        marker = ""
        if false_rate <= MAX_FALSE_HIT_RATE and (best is None or hit_rate > best[1]):
            best = (threshold, hit_rate)
            marker = "  <-"
        print(f"{threshold:>8.2f}{hit_rate:>9.0%}{false_rate:>10.0%}{marker}")

    if best is None:
        print(
            "\n오탐 0%를 만족하는 임계값이 없습니다. "
            "함정 쌍이 동의 쌍보다 유사도가 높다는 뜻이고, "
            "이 임베딩으로는 시맨틱 캐시를 안전하게 쓸 수 없습니다."
        )
        return

    threshold = best[0]
    print(f"\n선택 임계값: {threshold:.2f} (튜닝 쌍 기준 오탐 0%)")

    print(f"\n{'=' * 52}\n홀드아웃 검증 (임계값 산정에 쓰지 않은 쌍)\n{'=' * 52}")
    holdout_hit_rate = float((holdout_hit >= threshold).mean())
    holdout_false_rate = float((holdout_trap >= threshold).mean())
    exact_hit_rate = 0.0  # 정확 일치는 표현이 다르므로 한 건도 못 잡는다

    print(f"{'방법':<24}{'적중률':>10}{'오탐율':>10}")
    print(f"{'정확 일치 (기준선)':<24}{exact_hit_rate:>9.0%}{0.0:>10.0%}")
    print(f"{'시맨틱 캐시':<24}{holdout_hit_rate:>9.0%}{holdout_false_rate:>10.0%}")

    if holdout_false_rate > 0:
        print(
            f"\n! 홀드아웃에서 오탐 {holdout_false_rate:.0%} 발생 — "
            "튜닝 쌍 기준 0%는 낙관 편향이었다. 임계값을 올리거나 "
            "캐시 대상 의도를 줄일 것."
        )
        for (left, right), score in zip(holdout_trap_pairs := trap_holdout, holdout_trap):
            if score >= threshold:
                print(f'   {score:.4f}  "{left}"  <->  "{right}"')
    elif holdout_hit_rate > exact_hit_rate:
        print(
            f"\n=> 오탐 0%를 유지하며 적중률 {holdout_hit_rate:.0%} — "
            "정확 일치 대비 그만큼이 순이득이다."
        )
    else:
        print("\n=> 정확 일치 대비 이득이 없다. 시맨틱 캐시를 쓸 근거가 없다.")

    print("\n동의 쌍 중 놓친 것 (임계값 미만)")
    for (left, right), score in zip(synonym_holdout, holdout_hit):
        if score < threshold:
            print(f'   {score:.4f}  "{left}"  <->  "{right}"')


if __name__ == "__main__":
    main()
