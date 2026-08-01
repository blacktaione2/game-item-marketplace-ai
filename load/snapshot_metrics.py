"""`/metrics` 스냅샷을 뜨고 두 스냅샷을 차분한다.

## 왜 스크래핑이 아니라 스냅샷인가

Prometheus를 상시 띄우면 **부하테스트 중에 측정 대상과 부하 생성기에서 CPU를
뺏는다** — 배포 대상이 다른 프로젝트와 공유하는 4 OCPU다(ADR-0019). 그런데
히스토그램과 카운터는 **누적값**이라 실행 전후로 받아 빼면 그 구간의 정확한
집계가 나온다. 스크래핑 없이도 필요한 걸 얻는다.

실행:
    python snapshot_metrics.py before out/before.txt
    # ... 부하테스트 ...
    python snapshot_metrics.py after  out/after.txt
    python snapshot_metrics.py diff   out/before.txt out/after.txt

## 히스토그램 차분에서 주의할 것

`_sum`과 `_count`는 빼면 되지만 **평균은 빼면 안 된다** — 구간 평균은
`Δsum / Δcount`로 다시 계산해야 한다. `_max`는 gauge라 차분이 무의미해서
나중 값을 그대로 쓴다.
"""

from __future__ import annotations

import io
import re
import sys
from collections import defaultdict
from pathlib import Path
from urllib.request import urlopen

# 한글 출력이 콘솔 코드페이지(cp949)를 따라가면 깨지거나 터진다. 이 스크립트는
# README를 보고 남이 실행하므로 환경변수(PYTHONIOENCODING)에 의존하지 않고
# 여기서 고정한다. docs/05-Troubleshooting/한글-인코딩-windows-로케일-코덱.md
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# 카운터 순위를 단위군별로 가른다 (ADR-0025). 이름 접미사로 판별한다 —
# Prometheus 관례상 단위가 이름에 들어간다.
_COUNT, _BYTES, _TIME = "count", "bytes", "time"


def _collapse_equal(entries: list[tuple[str, float]]) -> list[tuple[str, float, int]]:
    """증가분이 같은 계열을 한 줄로 접는다.

    같은 값이면 담고 있는 정보도 같다. 인증을 붙이자 `spring_security_filterchains_*`
    20개 계열이 **전부 요청 수와 같은 값**으로 순위를 잠식했고, 그 바람에
    `rate_limited_total`이 표시에서 밀려나 오염 경고가 조용히 통과했다(ADR-0024).
    자리를 늘리는 대신 중복을 접는다.
    """
    collapsed: list[tuple[str, float, int]] = []
    for key, delta in entries:
        if collapsed and abs(collapsed[-1][1] - delta) < 1e-9:
            first, value, same = collapsed[-1]
            collapsed[-1] = (first, value, same + 1)
            continue
        collapsed.append((key, delta, 0))
    return collapsed


def _unit_group(key: str) -> str:
    name = key.split("{", 1)[0]
    if "_bytes" in name:
        return _BYTES
    if any(unit in name for unit in ("_seconds", "_ns", "_ms", "_time")):
        return _TIME
    return _COUNT


TARGETS = {
    "backend": "http://localhost:8080/actuator/prometheus",
    "ai": "http://localhost:8000/metrics",
}

_SAMPLE = re.compile(r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)(?P<labels>\{[^}]*\})? (?P<value>[^ ]+)$")


def fetch() -> str:
    parts = []
    for service, url in TARGETS.items():
        try:
            with urlopen(url, timeout=10) as response:
                body = response.read().decode("utf-8", errors="replace")
        except Exception as e:  # 한쪽만 떠 있어도 나머지는 받는다
            parts.append(f"# UNAVAILABLE {service}: {e}\n")
            continue
        parts.append(f"# SERVICE {service}\n{body}")
    return "\n".join(parts)


def parse(text: str) -> dict[str, float]:
    """`이름{라벨}` → 값. 주석과 파싱 불가 라인은 버린다."""
    samples: dict[str, float] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = _SAMPLE.match(line)
        if not match:
            continue
        try:
            value = float(match["value"])
        except ValueError:
            continue  # NaN, +Inf 등
        samples[f"{match['name']}{match['labels'] or ''}"] = value
    return samples


def diff(before: dict[str, float], after: dict[str, float]) -> None:
    counters: list[tuple[str, float]] = []
    histograms: dict[str, dict[str, float]] = defaultdict(dict)
    gauges: list[tuple[str, float, float]] = []

    for key, new in after.items():
        old = before.get(key, 0.0)
        base = key.split("{", 1)[0]

        if base.endswith("_max"):
            gauges.append((key, old, new))
            continue

        delta = new - old
        if base.endswith(("_sum", "_count")):
            # 히스토그램/서머리의 짝을 모아 구간 평균을 다시 계산한다.
            stem = base.rsplit("_", 1)[0]
            labels = key[len(base):]
            histograms[f"{stem}{labels}"][base.rsplit("_", 1)[1]] = delta
            continue
        if base.endswith("_bucket"):
            continue  # 분위수는 Prometheus 없이 안 쓴다. 합/횟수로 충분하다.
        if base.endswith("_created"):
            # prometheus_client가 메트릭마다 붙이는 생성 시각(유닉스 타임스탬프).
            # 차분하면 "10억 증가" 같은 값이 나와 카운터 목록을 덮는다.
            continue
        if abs(delta) > 1e-9:
            counters.append((key, delta))

    print(f"\n{'=' * 78}\n구간 집계 (after - before)\n{'=' * 78}")

    print("\n[타이머] Δ합계 / Δ횟수 = 구간 평균")
    print(f"  {'메트릭':<58}{'횟수':>7}{'평균':>11}")
    for key, pair in sorted(histograms.items()):
        count = pair.get("count", 0.0)
        total = pair.get("sum", 0.0)
        if count <= 0:
            continue
        print(f"  {key[:56]:<58}{count:>7.0f}{total / count:>10.4f}s")

    # **단위군을 갈라서 줄 세운다.** 예전에는 전부 한 순위에 넣고 상위 25개만
    # 뽑았는데, 바이트(억 단위)와 나노초가 요청 수(천 단위)를 언제나 밀어냈다.
    # 실제로 rate_limited 증가분 3,057이 표시에서 사라져 오염 경고가 조용히
    # 통과한 적이 있다(ADR-0024). 비교 가능한 것끼리만 줄 세워야 순위가 뜻을 갖는다.
    for title, group, top in (
        ("[카운터] 증가분 — 횟수", _COUNT, 20),
        ("[카운터] 증가분 — 바이트", _BYTES, 8),
        ("[카운터] 증가분 — 시간", _TIME, 8),
    ):
        selected = [(k, d) for k, d in counters if _unit_group(k) == group]
        if not selected:
            continue
        print(f"\n{title}")
        shown = _collapse_equal(sorted(selected, key=lambda e: -abs(e[1])))
        for key, delta, same in shown[:top]:
            suffix = f"  (+ 동일 증가분 {same}개)" if same else ""
            print(f"  {key[:66]:<68}{delta:>9.1f}{suffix}")
        if len(shown) > top:
            print(f"  … 이 군에서 {len(shown) - top}종 생략")

    moved = [(k, o, n) for k, o, n in gauges if abs(n - o) > 1e-9]
    if moved:
        print("\n[max 게이지] 차분이 아니라 나중 값 (구간 최댓값의 근사)")
        for key, _old, new in sorted(moved, key=lambda e: -e[2])[:10]:
            print(f"  {key[:66]:<68}{new:>9.4f}s")


def main() -> None:
    if len(sys.argv) < 3:
        print(__doc__)
        raise SystemExit(2)

    command = sys.argv[1]
    if command in {"before", "after", "snap"}:
        out = Path(sys.argv[2])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(fetch(), encoding="utf-8")
        print(f"스냅샷 저장: {out}")
    elif command == "diff":
        before = parse(Path(sys.argv[2]).read_text(encoding="utf-8"))
        after = parse(Path(sys.argv[3]).read_text(encoding="utf-8"))
        diff(before, after)
    else:
        raise SystemExit(f"알 수 없는 명령: {command}")


if __name__ == "__main__":
    main()
