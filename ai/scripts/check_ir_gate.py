"""IR 품질 게이트 — `evaluate_embedding --json` 이 낸 수치를 읽어 판정한다.

**수집과 채점을 분리한 이유**: 문턱을 고칠 때 재학습·재평가를 다시 하지 않기
위해서다. 이 저장소가 프롬프트 평가에서 이미 쓰는 규칙과 같다(`--score-only`).

실행:
  python -m scripts.check_ir_gate data/ir_metrics.json

**무엇을 단언하는가 — 그리고 왜 그것만인가**

1. 개선폭 > 0 (파인튜닝 모델이 베이스보다 낫다)
   같은 실행에서 두 모델을 같은 코드로 재므로 **머신 차이가 상쇄된다.**
   ADR-0007 이 실제로 하는 주장이기도 하다.
2. 절대 하한
   제품 속성이지만 머신 간 드리프트에 노출된다. 그래서 아래 실측 변동만큼
   여유를 두고 잡는다.
3. 공허함 방지 (아래)

**기록값 대비 무회귀는 걸지 않는다.** 기록된 0.3889 등은 학습 **1회**의 결과라,
그걸 문턱으로 쓰면 재학습 변동을 회귀로 오인한다.

**문턱의 근거 (2026-08-08, scripts/evaluate_training_variance.py, 학습 7회)**

  같은 시드 x2  : 가중치 해시 **동일** -> 변동 측정에 못 씀 (대조군)
  시드 x3       : recall@1 0.0185 / recall@5 0.0370 / mrr 0.0127
  스레드 1 vs 2 : 가중치 해시는 **다른데** 지표는 전부 동일 (mean_rank 까지)

두 번째 줄이 유일한 변동원이다. 세 번째 줄은 "머신이 달라지는 축"의 값싼 대리
측정인데 **0 이 나왔다** — 부동소수점 리덕션 순서가 바뀌어도 순위 순열이 안
바뀐다. 첫 줄이 대조군으로 필요한 이유는, 그냥 두 번 돌렸다면 나왔을 "변동 0"
이 측정 결과가 아니라 **구조**였기 때문이다.

  하한 = 관측 최솟값 - 2 x 변동폭

  recall@1  0.3889 - 2(0.0185) = 0.3519 -> 0.35
  recall@5  0.5556 - 2(0.0370) = 0.4816 -> 0.48
  mrr       0.4999 - 2(0.0127) = 0.4745 -> 0.47

**2배는 유도가 아니라 판단이다.** 시드 3개의 max-min 은 분포가 아니라 표본
3개의 범위이고, 범위는 표본이 적을수록 참값을 **과소추정**한다. 게다가 CI 러너는
여기서 못 잰 하드웨어라 남은 축이 있다(스레드 팔이 0 을 냈으니 작을 것으로
보지만, 작다와 0 은 다르다). 반올림은 전부 **내림**이다 — 하한에서 안전한 쪽.

**이 게이트가 잡는 회귀의 크기를 과장하지 않는다.** 여유가 이만큼이면 잡히는
것은 큰 회귀다: 코퍼스 훼손, 베이스 모델 오지정, 트리플 생성 깨짐, 학습이 사실상
안 된 경우. 미세한 품질 저하는 못 잡는다. 그건 홀드아웃 리포트를 사람이 읽는
일이고, CI 가 대신해줄 수 있다고 적어두면 거짓 안심이 된다.

**공허함 방지 검사**

이 저장소에는 평가 스크립트가 파인튜닝 모델을 **자기 자신과** 비교해 개선폭 0을
내면서도 실패하지 않은 전례가 있다(`docs/05-Troubleshooting/출력-경로를-입력으로-쓴-설정값.md`).
그래서 게이트는 판정 전에 **두 피연산자가 실제로 다른지** 먼저 본다. 이게 없으면
"모든 지표 통과"가 "아무것도 비교하지 않았다"와 구분되지 않는다.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 개선을 단언할 지표. mean_rank 는 부호가 반대라 따로 본다.
MUST_IMPROVE = ("recall@1", "recall@5", "mrr")

# 절대 하한. 위 docstring 의 실측에서 유도한다.
FLOOR = {
    "recall@1": 0.35,
    "recall@5": 0.48,
    "mrr": 0.47,
}

# 하한을 유도할 때 쓴 평가셋 크기. 이보다 **작아지면** 하한의 근거가 사라진다
# (질의 10건짜리 recall 은 0.1 단위로만 움직인다). 커지는 건 괜찮다.
MIN_QUERIES = 54


def evaluate_gate(data: dict, verbose: bool = True) -> list[str]:
    """실패 사유 목록을 낸다. 비어 있으면 통과다.

    판정과 출력을 한 함수에 두되 **종료는 하지 않는다** — 그래야 테스트가
    `SystemExit` 를 잡지 않고 사유 목록을 직접 볼 수 있다.
    """
    before, after = data["before"], data["after"]
    failures: list[str] = []

    def say(line: str = "") -> None:
        if verbose:
            print(line)

    say(f"질의 {data['n_queries']}건 / 코퍼스 {data['n_corpus']}건")
    say(f"베이스: {data['base_model']}")
    say(f"튜닝  : {data['tuned_path']}\n")

    # --- 0. 공허함 방지 ---------------------------------------------------
    # 경로가 같으면 아래 비교는 전부 자기 자신과의 비교다.
    if data["base_model"] == data["tuned_path"]:
        failures.append(
            "베이스와 튜닝 모델이 같은 경로다 — 비교가 성립하지 않는다 "
            "(embedding_base_model / embedding_model 설정 확인)"
        )
    # 경로가 달라도 수치가 전부 같으면 같은 가중치를 두 번 잰 것이다.
    if all(abs(after[k] - before[k]) < 1e-9 for k in before):
        failures.append(
            "모든 지표가 소수점까지 동일하다 — 같은 모델을 두 번 잰 것으로 보인다"
        )
    # 평가셋이 줄면 아래 하한은 유도 근거를 잃는다.
    if data["n_queries"] < MIN_QUERIES:
        failures.append(
            f"평가 질의가 {data['n_queries']}건으로 하한 유도 시점({MIN_QUERIES}건)보다 "
            "적다 — 하한을 다시 유도해야 한다 (scripts/evaluate_training_variance.py)"
        )

    # --- 1. 개선폭 > 0 ----------------------------------------------------
    say(f"{'지표':<11} {'전':>9} {'후':>9} {'변화':>9}   판정")
    say("-" * 52)
    for m in MUST_IMPROVE:
        delta = after[m] - before[m]
        ok = delta > 0
        say(f"{m:<11} {before[m]:>9.4f} {after[m]:>9.4f} {delta:>+9.4f}   "
            f"{'PASS' if ok else 'FAIL'}")
        if not ok:
            failures.append(f"{m} 가 베이스 대비 개선되지 않았다 ({delta:+.4f})")

    # --- 2. 절대 하한 -----------------------------------------------------
    say()
    for m, floor in FLOOR.items():
        ok = after[m] >= floor
        say(f"{m:<11} {after[m]:>9.4f}  하한 {floor:.4f}   "
            f"{'PASS' if ok else 'FAIL'}")
        if not ok:
            failures.append(f"{m} 가 하한 {floor:.4f} 아래다 ({after[m]:.4f})")

    return failures


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("metrics_json")
    args = parser.parse_args()

    path = Path(args.metrics_json)
    if not path.exists():
        # 파일이 없는 것은 통과가 아니다 — 평가가 안 돌았다는 뜻이다.
        raise SystemExit(f"지표 파일이 없습니다: {path}")

    failures = evaluate_gate(json.loads(path.read_text(encoding="utf-8")))

    print("\n" + "-" * 52)
    if failures:
        # **판정에 쓴 값을 전부 낸다.** 한 줄짜리 실패는 사람이 다시 조사해야 한다.
        for f in failures:
            print(f"  [FAIL] {f}")
        print(f"\n실패 {len(failures)}건")
        sys.exit(1)
    print("IR 게이트 통과")


if __name__ == "__main__":
    main()
