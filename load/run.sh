#!/usr/bin/env bash
# 부하테스트 1회 실행 = 스냅샷 + k6 + 스냅샷 + 차분 + 재고 정합성 확인.
#
# 정합성 확인을 여기 넣은 이유: **성공 응답 수와 재고 감소분이 같은지**가 이
# 라운드의 핵심 단언(ADR-0001)인데, 손으로 하면 빼먹는다. 실행 절차에 박아둔다.
#
# 사용:
#   ./load/run.sh purchase contended 20 30s
#   ./load/run.sh purchase spread    20 30s
#   ./load/run.sh purchase contended step        # 계단식 knee 탐색
#   ./load/run.sh ai cache-warm 10 40s
#   ./load/run.sh ai live-llm   3  30s
set -uo pipefail

SUITE="${1:?purchase | ai}"
MODE="${2:?contended|spread|step | cache-warm|live-llm}"
VUS="${3:-20}"
DURATION="${4:-30s}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${OUT_DIR:-$ROOT/load/out}"
PY="$ROOT/ai/.venv/Scripts/python.exe"
[ -x "$PY" ] || PY="python"
mkdir -p "$OUT"

TAG="${SUITE}-${MODE}-$(date +%H%M%S)"

snapshot() { "$PY" "$ROOT/load/snapshot_metrics.py" "$1" "$OUT/$TAG.$2" >/dev/null; }

# **부하 아이템 전체의 재고 합**을 본다. spread 프로파일은 9002~9021로 흩어져
# 사므로 9001만 보면 감소분을 놓친다(워밍업만 9001을 친다). 합을 쓰면 두
# 프로파일 모두 "판 만큼 줄었나"가 한 식으로 성립한다.
stock() { docker exec gimp-postgres psql -U gimp -d gimp -t \
    -c "SELECT COALESCE(SUM(stock),0) FROM items WHERE id BETWEEN 9001 AND 9021;" \
    2>/dev/null | tr -d ' \r\n'; }

echo "=== $TAG ==="
STOCK_BEFORE="$(stock)"
snapshot before before.txt

if [ "$SUITE" = "purchase" ]; then
  if [ "$MODE" = "step" ]; then
    k6 run -e PROFILE=contended -e STAGES=step \
      "$ROOT/load/k6/purchase-contention.js" 2>&1 | tee "$OUT/$TAG.k6.txt"
  else
    k6 run -e "PROFILE=$MODE" -e "VUS=$VUS" -e "DURATION=$DURATION" \
      "$ROOT/load/k6/purchase-contention.js" 2>&1 | tee "$OUT/$TAG.k6.txt"
  fi
else
  k6 run -e "MODE=$MODE" -e "VUS=$VUS" -e "DURATION=$DURATION" \
    "$ROOT/load/k6/ai-search.js" 2>&1 | tee "$OUT/$TAG.k6.txt"
fi

snapshot after after.txt
STOCK_AFTER="$(stock)"

"$PY" "$ROOT/load/snapshot_metrics.py" diff \
  "$OUT/$TAG.before.txt" "$OUT/$TAG.after.txt" | tee "$OUT/$TAG.diff.txt"

if [ "$SUITE" = "purchase" ] && [ -n "$STOCK_BEFORE" ] && [ -n "$STOCK_AFTER" ]; then
  CREATED="$(grep -oE 'created_201[^0-9]*([0-9]+)' "$OUT/$TAG.k6.txt" | grep -oE '[0-9]+$' | tail -1)"
  SOLD=$((STOCK_BEFORE - STOCK_AFTER))
  # setup()의 워밍업이 실제 구매 1건을 낸다. k6 커스텀 메트릭은 setup()에서
  # 집계되지 않으므로 created_201에 안 잡힌다. 워밍업이 락 경로 자체를 데우는
  # 게 목적이라 GET으로 바꾸지 않고, 대신 기대식에 명시한다.
  WARMUP_PURCHASES=1
  EXPECTED=$(( ${CREATED:-0} + WARMUP_PURCHASES ))
  echo
  echo "=============================================================================="
  echo "정합성 단언 (ADR-0001) — 오버셀이 일어났는가"
  echo "=============================================================================="
  echo "  재고 감소분              $SOLD   ($STOCK_BEFORE -> $STOCK_AFTER)"
  echo "  201 응답 + 워밍업 1건    $EXPECTED   (${CREATED:-?} + $WARMUP_PURCHASES)"
  if [ "$EXPECTED" = "$SOLD" ]; then
    echo "  => 일치. **오버셀 0건** — 성공한 구매만큼만 재고가 줄었다."
  else
    echo "  => **불일치 $((SOLD - EXPECTED))건. 결함이다.**"
    echo "     재고가 더 줄었으면 락이 샌 것이고, 덜 줄었으면 커밋이 유실됐다."
  fi
fi
echo
echo "산출물: $OUT/$TAG.*"
