"""재학습이 IR 지표를 얼마나 흔드는가 — CI 문턱의 근거를 만든다.

**왜 이게 필요한가**

`.github/workflows/ai.yml` 의 IR 게이트는 매 커밋 모델을 재학습해 Recall@k /
MRR 에 문턱을 건다. 문턱을 정하려면 "재학습만으로 수치가 얼마나 움직이는가"를
알아야 한다. 이 저장소는 잡음을 재지 않고 문턱을 정했다가 사전 등록 기준이
실패한 전례가 두 번 있다(ADR-0028 의 틱 max, ADR-0040 의 타속성 95%).
**derivation 에 잡음이 안 나오는 문턱은 derivation 이 없는 것이다.**

**세 갈래로 재는 이유**

`finetune_embedding` 은 시드가 42로 고정돼 있다. 그러니 같은 박스에서 그냥 두 번
돌리면 같은 모델이 나올 수 있고, 그때 "변동 0" 은 **측정 결과가 아니라 구조**다.
셋을 나눠 재야 그 구분이 선다:

  same-seed  : 같은 시드 반복      -> 재학습이 국소적으로 결정적인가 (대조군)
  diff-seed  : 시드만 변경          -> 학습 자체의 변동
  threads    : OMP_NUM_THREADS 변경 -> BLAS 리덕션 순서가 바뀐다.
               **다른 머신의 값싼 대리 측정**이다. CI 러너는 같은 시드를 다른
               하드웨어에서 돌리므로, 이쪽이 실제 CI 가 겪는 축에 더 가깝다.

세 갈래 전부에서 **모델 파일 해시**를 같이 낸다. 수치가 같을 때 "안정적이라
같다"와 "애초에 같은 파일이라 같다"를 구분하지 못하면 아무것도 잰 게 아니다.

실행:
  python -m scripts.evaluate_training_variance              # 전부
  python -m scripts.evaluate_training_variance --arms same-seed
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from app.corpus import ALL_ITEMS
from app.core.config import get_settings
from app.services.training.evaluation import evaluate_model
from app.services.training.hard_negatives import item_text

# 문턱을 걸 후보 지표. mean_rank 는 낮을수록 좋아 부호가 반대라 여기 넣지 않는다.
METRICS = ("recall@1", "recall@3", "recall@5", "recall@10", "mrr")


def _run_arms(arm: str) -> list[dict]:
    """각 팔이 돌릴 (이름, 시드, 스레드수) 목록."""
    if arm == "same-seed":
        return [{"label": "seed42-a", "seed": 42}, {"label": "seed42-b", "seed": 42}]
    if arm == "diff-seed":
        return [{"label": f"seed{s}", "seed": s} for s in (1, 2, 3)]
    if arm == "threads":
        return [
            {"label": "seed42-thr1", "seed": 42, "threads": 1},
            {"label": "seed42-thr2", "seed": 42, "threads": 2},
        ]
    raise SystemExit(f"모르는 팔: {arm}")


def _sha256_of_weights(model_dir: Path) -> str:
    """가중치 파일의 해시. 이름은 세이브 포맷에 따라 달라질 수 있다."""
    candidates = sorted(model_dir.glob("*.safetensors")) or sorted(
        model_dir.glob("pytorch_model.bin")
    )
    if not candidates:
        return "(가중치 파일 없음)"
    h = hashlib.sha256()
    for path in candidates:
        h.update(path.read_bytes())
    return h.hexdigest()[:16]


def _train(out_dir: Path, seed: int, threads: int | None) -> None:
    env = dict(os.environ)
    if threads is not None:
        # torch 는 이 둘을 다 본다. 하나만 두면 intra-op 스레드가 안 바뀐다.
        env["OMP_NUM_THREADS"] = str(threads)
        env["MKL_NUM_THREADS"] = str(threads)
    cmd = [
        sys.executable, "-m", "scripts.finetune_embedding",
        "--out", str(out_dir), "--seed", str(seed),
    ]
    # **한글 stderr 를 text=True 로 읽으면 cp949 경계에서 죽는다.** 바이트로 받아
    # 실패했을 때만 디코드한다.
    done = subprocess.run(cmd, capture_output=True, env=env)
    if done.returncode != 0:
        sys.stderr.write(done.stderr.decode("cp949", errors="replace"))
        raise SystemExit(f"학습 실패 (seed={seed}, threads={threads})")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--arms",
        nargs="*",
        default=["same-seed", "diff-seed", "threads"],
        help="same-seed / diff-seed / threads",
    )
    parser.add_argument("--eval-queries", default="data/eval_queries.jsonl")
    parser.add_argument("--out", default="data/training_variance.json")
    args = parser.parse_args()

    from sentence_transformers import SentenceTransformer

    rows = [json.loads(line) for line in io.open(args.eval_queries, encoding="utf-8")]
    queries = [r["query"] for r in rows]
    corpus_texts = [item_text(i) for i in ALL_ITEMS]
    id_to_index = {item["item_id"]: idx for idx, item in enumerate(ALL_ITEMS)}
    gold_indices = [id_to_index[r["gold_item_id"]] for r in rows]

    # 기준선. 학습과 무관하게 상수이므로 한 번만 잰다.
    settings = get_settings()
    base = SentenceTransformer(settings.embedding_base_model)
    before, _ = evaluate_model(base, queries, gold_indices, corpus_texts)
    print(f"질의 {len(queries)}건 / 코퍼스 {len(corpus_texts)}건")
    print(f"파인튜닝 전 (상수): {before}\n")

    results: dict[str, list[dict]] = {}
    workdir = Path(tempfile.mkdtemp(prefix="variance-"))
    try:
        for arm in args.arms:
            print(f"== {arm} ==")
            results[arm] = []
            for spec in _run_arms(arm):
                out_dir = workdir / spec["label"]
                _train(out_dir, spec["seed"], spec.get("threads"))
                digest = _sha256_of_weights(out_dir)
                model = SentenceTransformer(str(out_dir))
                after, _ = evaluate_model(model, queries, gold_indices, corpus_texts)
                print(f"  {spec['label']:<14} sha={digest}  {after}")
                results[arm].append(
                    {**spec, "weights_sha256": digest, "metrics": after}
                )
                shutil.rmtree(out_dir, ignore_errors=True)
            print()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print("=" * 62)
    print("팔별 변동 폭 (max - min)")
    print("=" * 62)
    worst: dict[str, float] = {m: 0.0 for m in METRICS}
    for arm, runs in results.items():
        digests = {r["weights_sha256"] for r in runs}
        # **해시가 하나면 수치가 같은 건 당연하다.** 그걸 "안정적"이라고 읽으면
        # 측정한 게 없다.
        same = len(digests) == 1
        print(f"\n{arm} (n={len(runs)}, 가중치 {'동일' if same else '상이'})")
        for m in METRICS:
            values = [r["metrics"][m] for r in runs]
            spread = max(values) - min(values)
            if not same:
                worst[m] = max(worst[m], spread)
            note = "  <- 가중치가 같아 변동 측정에 못 씀" if same else ""
            print(f"  {m:<11} {min(values):.4f} ~ {max(values):.4f}  폭 {spread:+.4f}{note}")

    print("\n" + "=" * 62)
    print("문턱 근거: 가중치가 실제로 달라진 팔들의 최대 변동 폭")
    print("=" * 62)
    for m in METRICS:
        print(f"  {m:<11} {worst[m]:.4f}")

    Path(args.out).write_text(
        json.dumps(
            {"before": before, "arms": results, "worst_spread": worst},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n저장: {args.out}")


if __name__ == "__main__":
    main()
