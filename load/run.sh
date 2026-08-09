#!/usr/bin/env bash
# 부하테스트 1회 실행 = 스냅샷 + k6 + 스냅샷 + 차분 + 재고 정합성 확인.
#
# 정합성 확인을 여기 넣은 이유: **성공 응답 수와 재고 감소분이 같은지**가 이
# 라운드의 핵심 단언(ADR-0001)인데, 손으로 하면 빼먹는다. 실행 절차에 박아둔다.
#
# 사용:
#   ./load/run.sh purchase contended 20 30s
#   ./load/run.sh purchase spread    20 30s
#   ./load/run.sh purchase step                  # 계단식 knee 탐색
#     (오래 `purchase contended step` 이라고 적혀 있었는데 **그건 동작하지 않는다** —
#      step 분기는 MODE 로 갈리고, 세 번째 자리의 step 은 VUS 로 넘어가 NaN 이 됐다)
#   ./load/run.sh ai cache-warm 10 40s
#   ./load/run.sh ai live-llm   3  30s
set -uo pipefail

SUITE="${1:?purchase | ai}"
MODE="${2:?contended|spread|step | cache-warm|live-llm}"

# **모르는 스위트 이름을 조용히 넘기지 않는다.** 예전에는 else 분기가 전부 AI를
# 돌려서, `backend contended`(존재하지 않는 이름)를 주면 구매 부하 대신 **AI
# 부하**가 돌았다 — 즉 실수가 OpenAI 과금과 20분짜리 오해로 이어졌다. 실제로
# 한 번 그렇게 태웠고, 다행히 대부분이 429로 막혀 실호출은 26건이었다.
case "$SUITE" in
  purchase|ai) ;;
  *) echo "알 수 없는 스위트: '$SUITE' (purchase | ai)" >&2; exit 2 ;;
esac

# **모드도 같은 이유로 막는다.** 위 가드는 스위트에만 걸려 있었는데, 아래
# 분기는 모드를 그대로 k6 에 넘기고 k6 는 모르는 값을 기본값으로 조용히
# 대체한다(`ai-search.js` 는 `cache-warm`, `purchase-contention.js` 는
# `contended`). 즉 `ai live-lmm` 같은 오타는 **다른 프로파일을 돌리고도 정상
# 종료한다** — 스위트 오타로 이미 한 번 겪은 그 일이 한 축 옆에 그대로 남아
# 있었다. *가드를 한 축에만 걸면 이웃 축은 안 걸린 채로 남는다.*
case "$SUITE:$MODE" in
  purchase:contended|purchase:spread|purchase:step) ;;
  ai:cache-warm|ai:live-llm) ;;
  *) echo "알 수 없는 모드: '$SUITE $MODE'" >&2
     echo "  purchase → contended | spread | step" >&2
     echo "  ai       → cache-warm | live-llm" >&2
     exit 2 ;;
esac

# **기본값을 여기서 만들지 않는다.** 예전에는 `VUS="${3:-20}"` 였는데, 그 20이
# k6 스크립트의 기본값을 덮어썼다. `ai-search.js` 는 `live-llm` 일 때 **3**을
# 기본으로 두고 있다 — OpenAI 를 20 동시로 때리지 않으려고 일부러 그렇게 둔
# 값인데, run.sh 를 거치면 그 방어가 죽은 코드가 됐다. 문서화된 호출
# (`./load/run.sh ai live-llm 3 30s`)은 네 인자를 다 적었을 때만 맞았고,
# 생략하면 조용히 20이 나갔다.
#
# 그래서 **주어졌을 때만 넘긴다.** 기본값은 각 k6 스크립트가 소유한다 —
# 같은 숫자를 두 곳에 두면 한쪽만 바뀌는 게 이 저장소의 단골 결함이다.
VUS="${3:-}"
DURATION="${4:-}"

# **인자 자리를 틀리는 것도 오타다.** 위 가드는 모드 *이름*만 봤는데, 이 파일의
# 헤더가 오랫동안 `purchase contended step` 을 사용법으로 적고 있었다 — 실제
# 계단식 모드는 `purchase step` 이다. 세 번째 자리에 온 `step` 은 그대로
# `-e VUS=step` 이 되고 k6 는 `Number("step")` = **NaN** 으로 돈다. 가드를 한 축에만
# 걸면 이웃 축이 남는다는 그 이야기가, 가드를 고친 라운드에도 한 겹 더 있었다.
case "$VUS" in
  ""|*[!0-9]*) [ -z "$VUS" ] || { echo "VUS 는 숫자여야 합니다: '$VUS'" >&2
                                  echo "  계단식은 './load/run.sh purchase step' 입니다" >&2
                                  exit 2; } ;;
esac

K6_ARGS=()
[ -n "$VUS" ] && K6_ARGS+=(-e "VUS=$VUS")
[ -n "$DURATION" ] && K6_ARGS+=(-e "DURATION=$DURATION")

# 두 시나리오 모두 setup()에서 로그인한다 (ADR-0031). **여기서 먼저 막는다** —
# 없이 돌리면 k6 의 setup 예외로 나타나서 원인이 부하 스크립트처럼 보인다.
#
# k6 는 시스템 환경변수를 __ENV 에 넣어주지만 그 기본값에 기대지 않고 -e 로
# 명시해 넘긴다. 이 저장소는 "환경변수가 자동 상속될 것"이라는 가정으로 이미
# 세 번 틀렸다(RABBITMQ_HOST · DEMO_PASSWORD · DB_PASSWORD, 전부 compose).
: "${DEMO_PASSWORD:?DEMO_PASSWORD 가 필요합니다 — 두 시나리오 모두 로그인합니다 (ADR-0031)}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${OUT_DIR:-$ROOT/load/out}"
# venv 레이아웃이 OS 마다 다르고(`Scripts` vs `bin`), **리눅스에는 `python` 이라는
# 이름이 없다**(우분투는 `python3` 뿐). 예전 판본은 Windows 경로 + `python` 폴백뿐이라
# 배포 대상에서 스냅샷 수집이 조용히 실패할 상태였다.
PY="$ROOT/ai/.venv/Scripts/python.exe"          # Windows venv
[ -x "$PY" ] || PY="$ROOT/ai/.venv/bin/python"  # Linux venv
if [ ! -x "$PY" ]; then
  # **이름이 아니라 실행 여부로 고른다.** Windows 에는 실행되지 않는 스토어 스텁
  # `python3` 이 있어서 `command -v` 만으로 고르면 그걸 잡는다.
  PY=""
  for _c in python3 python; do
    command -v "$_c" >/dev/null 2>&1 && "$_c" -c '' >/dev/null 2>&1 && { PY="$_c"; break; }
  done
fi
[ -n "$PY" ] || { echo "동작하는 python3/python 이 필요합니다" >&2; exit 1; }
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
    # **계단식은 VUS/DURATION 을 쓰지 않는다** — 단계 표(`STEP_STAGES`)가 대신
    # 정한다. 그래서 주면 조용히 무시되는데, 그게 이 파일이 방금 고친 결함의
    # 거울상이다(`purchase contended step` 이 VUS 로 흘러 NaN 이 됐던 것).
    # 무시할 거면 무시한다고 말한다.
    [ ${#K6_ARGS[@]} -gt 0 ] && echo "  (step 모드는 VUS/DURATION 을 쓰지 않습니다 — 무시합니다)" >&2
    k6 run -e PROFILE=contended -e STAGES=step -e "DEMO_PASSWORD=$DEMO_PASSWORD" \
      "$ROOT/load/k6/purchase-contention.js" 2>&1 | tee "$OUT/$TAG.k6.txt"
  else
    k6 run -e "PROFILE=$MODE" "${K6_ARGS[@]}" \
      -e "DEMO_PASSWORD=$DEMO_PASSWORD" \
      "$ROOT/load/k6/purchase-contention.js" 2>&1 | tee "$OUT/$TAG.k6.txt"
  fi
else
  k6 run -e "MODE=$MODE" "${K6_ARGS[@]}" \
    -e "DEMO_PASSWORD=$DEMO_PASSWORD" \
    "$ROOT/load/k6/ai-search.js" 2>&1 | tee "$OUT/$TAG.k6.txt"
fi

snapshot after after.txt
STOCK_AFTER="$(stock)"

"$PY" "$ROOT/load/snapshot_metrics.py" diff \
  "$OUT/$TAG.before.txt" "$OUT/$TAG.after.txt" | tee "$OUT/$TAG.diff.txt"

# --- 오염 확인: 리미터에 걸렸는가 (ADR-0024) --------------------------------
#
# 한도가 운영값이면 부하테스트는 **리미터를 측정**하게 된다. ADR-0020에서 "락
# 경합인 줄 알았더니 재고 고갈을 재고 있던" 것과 같은 종류의 오염인데, 이쪽은
# 429가 조용히 섞이기 때문에 요약만 봐서는 더 안 보인다. 사람이 눈치채길
# 기대하지 않고 여기서 단언한다.
# **차분 출력이 아니라 스냅샷 원본에서 센다.** diff.txt는 증가분 상위 25개만
# 보여주는데, 그 순위에 바이트 카운터(억 단위)가 섞여 있어서 요청 수 단위 카운터는
# 쉽게 밀려난다 — 실제로 rate_limited 증가분 3,057이 표시에서 사라졌다.
# 안전망이 잘린 화면을 입력으로 쓰면 안전망이 아니다.
LIMITED="$("$PY" -c "
import re, sys
def total(path):
    s = 0
    for line in open(path, encoding='utf-8'):
        if line.startswith('#'):
            continue
        m = re.match(r'^(ai_)?rate_limited_total\{[^}]*\}\s+([0-9.eE+-]+)', line)
        if m:
            s += float(m.group(2))
    return s
print(int(total(sys.argv[2]) - total(sys.argv[1])))
" "$OUT/$TAG.before.txt" "$OUT/$TAG.after.txt" 2>/dev/null)"
if [ -n "$LIMITED" ] && [ "$LIMITED" != "0" ]; then
  echo
  echo "=============================================================================="
  echo "!! 오염 경고 — 한도 초과 거절이 $LIMITED 건 발생했다"
  echo "=============================================================================="
  echo "  이 측정은 시스템이 아니라 리미터를 재고 있다."
  echo "  백엔드: SPRING_PROFILES_ACTIVE=loadtest 로 띄웠는지 확인"
  echo "  AI    : RATE_LIMIT_ASSISTANT_PER_MIN 을 올려서 띄웠는지 확인"
  echo "  자세한 절차는 load/README.md"
fi

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
