"""IR 품질 게이트가 **실제로 떨어뜨리는지** 확인한다.

**통과만 본 검사는 항상 통과하는 검사와 구분되지 않는다.** 이 저장소는 검사
자체가 틀린 사례를 29건 모아뒀고, 그중 여럿이 "실패하는 입력을 한 번도 안
넣어봤다"에서 나왔다. 그래서 여기서는 통과 케이스 하나마다 **떨어지는 케이스를
같이** 넣는다.

게이트가 CI 에서만 돌기 때문에 더 그렇다 — 잘못 만들면 초록만 보다가 임베딩
품질 회귀를 놓친다.
"""

from __future__ import annotations

import copy

from scripts.check_ir_gate import FLOOR, MIN_QUERIES, evaluate_gate
from scripts.evaluate_embedding import _write_step_summary

# 2026-08-08 실측을 본뜬 통과 표본.
PASSING = {
    "n_queries": 54,
    "n_corpus": 42,
    "base_model": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    "tuned_path": "models/embedding-finetuned",
    "before": {
        "recall@1": 0.1481, "recall@3": 0.3148, "recall@5": 0.4444,
        "recall@10": 0.6111, "mrr": 0.3005, "mean_rank": 10.72,
    },
    "after": {
        "recall@1": 0.3889, "recall@3": 0.5370, "recall@5": 0.6111,
        "recall@10": 0.7778, "mrr": 0.5099, "mean_rank": 7.24,
    },
}


def _case(**after_overrides) -> dict:
    data = copy.deepcopy(PASSING)
    data["after"].update(after_overrides)
    return data


class TestPasses:
    def test_실측값은_통과한다(self):
        # 이게 없으면 아래 실패 케이스들이 "게이트가 늘 떨어진다"와 구분되지 않는다.
        assert evaluate_gate(PASSING, verbose=False) == []


class TestVacuityGuards:
    """게이트가 **아무것도 비교하지 않은 상태**를 통과로 읽지 않는지."""

    def test_베이스와_튜닝_경로가_같으면_떨어진다(self):
        data = copy.deepcopy(PASSING)
        data["tuned_path"] = data["base_model"]
        failures = evaluate_gate(data, verbose=False)
        assert any("같은 경로" in f for f in failures)

    def test_전후_수치가_전부_같으면_떨어진다(self):
        # 실제로 있었던 결함이다 — 평가 스크립트가 파인튜닝 모델을 자기 자신과
        # 비교해 개선폭 0을 내면서도 실패하지 않았다.
        data = copy.deepcopy(PASSING)
        data["after"] = copy.deepcopy(data["before"])
        failures = evaluate_gate(data, verbose=False)
        assert any("동일" in f for f in failures)

    def test_평가셋이_줄면_떨어진다(self):
        data = copy.deepcopy(PASSING)
        data["n_queries"] = MIN_QUERIES - 1
        failures = evaluate_gate(data, verbose=False)
        assert any("하한을 다시 유도" in f for f in failures)

    def test_평가셋이_늘면_통과한다(self):
        # 하한의 근거를 잃는 건 **줄 때**뿐이다. 늘어난 걸 막으면 평가셋 확장이
        # 게이트에 막힌다.
        data = copy.deepcopy(PASSING)
        data["n_queries"] = MIN_QUERIES + 20
        assert evaluate_gate(data, verbose=False) == []


class TestCatchesRegression:
    def test_개선폭이_0이면_떨어진다(self):
        data = _case(**{"recall@1": PASSING["before"]["recall@1"]})
        failures = evaluate_gate(data, verbose=False)
        assert any("개선되지 않았다" in f for f in failures)

    def test_개선폭이_음수면_떨어진다(self):
        data = _case(mrr=0.1)
        failures = evaluate_gate(data, verbose=False)
        assert any("mrr" in f and "개선되지 않았다" in f for f in failures)

    def test_하한_아래면_떨어진다(self):
        # 베이스보다는 나은데 하한에는 못 미치는 상태. 두 검사가 **다른 것을**
        # 보고 있다는 확인이기도 하다 — 하나로 합칠 수 없는 이유다.
        target = "recall@5"
        below = round(FLOOR[target] - 0.01, 4)
        assert below > PASSING["before"][target], (
            "이 케이스가 성립하려면 하한이 베이스보다 위여야 한다"
        )
        data = _case(**{target: below})
        failures = evaluate_gate(data, verbose=False)
        assert any("하한" in f for f in failures)
        assert not any("개선되지 않았다" in f for f in failures)


class TestRagasSummary:
    """RAGAS 요약 렌더러 — **한 번 돌리는 데 2시간이 걸려서** 여기서 검증한다.

    실제 실행으로만 확인하려 들면 결과를 못 읽는 결함이 다음 실행까지 안 보인다.
    """

    RAGAS = {
        "before": {"context_precision": 0.2935, "context_recall": 0.4630},
        "after": {"context_precision": 0.5133, "context_recall": 0.6111},
        "n_calls_estimated": 648,
        "error_rate": 0.0,
        "trustworthy": True,
    }

    def test_요약이_없으면_아무것도_안_쓴다(self, tmp_path, monkeypatch):
        # 로컬 실행에서 환경변수가 없다 — 그때 죽으면 안 된다.
        monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
        _write_step_summary(self.RAGAS)  # 예외가 안 나면 통과

    def test_점수와_호출수가_요약에_들어간다(self, tmp_path, monkeypatch):
        out = tmp_path / "summary.md"
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(out))
        _write_step_summary(self.RAGAS)
        text = out.read_text(encoding="utf-8")
        assert "0.5133" in text and "0.6111" in text
        assert "648" in text
        # **게이트가 아니라는 것이 요약에 적혀 있어야 한다.** 표만 보면 판정처럼
        # 읽히고, 이 프로젝트가 RAGAS 를 내린 이유가 통째로 사라진다.
        assert "게이트" in text

    def test_실패율이_높으면_요약이_경고한다(self, tmp_path, monkeypatch):
        out = tmp_path / "summary.md"
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(out))
        _write_step_summary({**self.RAGAS, "error_rate": 0.4, "trustworthy": False})
        text = out.read_text(encoding="utf-8")
        assert "신뢰할 수 없다" in text, "실패율이 높은데 표가 그대로 읽히면 안 된다"


class TestFloorsAreDerived:
    def test_하한은_실측_아래에_있다(self):
        # 하한이 실측값 위에 있으면 게이트는 첫 실행부터 떨어진다. ADR-0040 에서
        # 사전 등록 기준을 **구조적으로 만족 불가능하게** 써서 실패한 전례가 있다.
        for metric, floor in FLOOR.items():
            assert floor < PASSING["after"][metric], (
                f"{metric} 하한 {floor} 이 실측 {PASSING['after'][metric]} 이상이다"
            )

    def test_하한은_베이스보다_위에_있다(self):
        # 하한이 베이스 아래면 "파인튜닝을 안 한 모델"도 통과한다.
        for metric, floor in FLOOR.items():
            assert floor > PASSING["before"][metric], (
                f"{metric} 하한 {floor} 이 베이스 {PASSING['before'][metric]} 이하다"
            )
