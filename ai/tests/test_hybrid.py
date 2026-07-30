"""RRF 융합 단위 테스트.

ADR-0005가 "순수 함수라 단위 테스트가 쉽다"며 두 가지 성질을 검증했다고
적어두었는데, 정작 그 테스트가 리포지토리에 없었다(임시로 돌리고 남기지
않았다). 문서의 주장을 코드로 뒷받침하기 위해 복원한다.

특히 `test_first_plus_third_beats_second_plus_second`는 처음 검증할 때
**직관이 틀렸던 케이스**다. "2등+2등이 1등+3등보다 낫다"고 단정했는데
1/(k+1) + 1/(k+3) > 2/(k+2) 라서 반대였다. 구현이 아니라 내 기대가 틀렸던
것이고, 같은 착각을 다시 하지 않도록 회귀 테스트로 박아둔다.
"""

from app.services.search.hybrid import RRF_RANK_CONSTANT, reciprocal_rank_fusion


def scores(fused):
    return dict(fused)


def test_empty_input_returns_empty():
    assert reciprocal_rank_fusion([]) == []
    assert reciprocal_rank_fusion([[], []]) == []


def test_single_list_preserves_order():
    fused = reciprocal_rank_fusion([["a", "b", "c"]])
    assert [doc_id for doc_id, _ in fused] == ["a", "b", "c"]


def test_output_sorted_by_score_descending():
    fused = reciprocal_rank_fusion([["a", "b", "c"], ["c", "b", "a"]])
    values = [score for _, score in fused]
    assert values == sorted(values, reverse=True)


def test_symmetric_inputs_tie_exactly():
    """두 검색기가 정확히 뒤집힌 순위를 주면 양 끝 문서는 동점이어야 한다.

    a는 1등+3등, c는 3등+1등이므로 기여도 합이 같다. 부동소수 오차 없이
    정확히 같아야 한다 — 덧셈 순서만 다른 같은 항들이기 때문이다.
    """
    fused = scores(reciprocal_rank_fusion([["a", "b", "c"], ["c", "b", "a"]]))
    assert fused["a"] == fused["c"]


def test_document_in_both_lists_beats_single_list_winner():
    """양쪽에 2등으로 오른 문서가, 한쪽에만 1등으로 오른 문서를 이긴다.

    RRF의 핵심 성질이다 — 두 검색기가 함께 지지하는 문서를 우대한다.
    both:   2/(60+2) = 0.032258
    single: 1/(60+1) = 0.016393
    """
    fused = scores(
        reciprocal_rank_fusion([["single", "both"], ["other", "both"]])
    )
    assert fused["both"] > fused["single"]


def test_first_plus_third_beats_second_plus_second():
    """1등+3등이 2등+2등을 이긴다. 직관과 반대이므로 회귀 테스트로 고정한다.

    1/(k+1) + 1/(k+3) 와 2/(k+2)의 비교는 1/x의 볼록성 문제다. 볼록 함수는
    같은 평균을 갖는 두 점의 함숫값 평균이 중점의 함숫값보다 크다.
    k=60이면 0.0322665 > 0.0322581 로 차이가 아주 작지만 방향은 분명하다.
    """
    k = RRF_RANK_CONSTANT
    spread = 1.0 / (k + 1) + 1.0 / (k + 3)
    centered = 2.0 / (k + 2)
    assert spread > centered

    fused = scores(
        reciprocal_rank_fusion(
            [
                ["spread", "centered", "filler"],
                ["filler", "centered", "spread"],
            ]
        )
    )
    assert fused["spread"] > fused["centered"]


def test_rank_constant_dampens_top_rank_advantage():
    """k가 클수록 상위 순위의 우위가 줄어든다 — 튜닝 파라미터인 이유."""
    ranked = [["a", "b"]]
    small_k = scores(reciprocal_rank_fusion(ranked, rank_constant=1))
    large_k = scores(reciprocal_rank_fusion(ranked, rank_constant=1000))
    assert small_k["a"] / small_k["b"] > large_k["a"] / large_k["b"]
