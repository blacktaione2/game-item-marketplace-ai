#!/usr/bin/env bash
# SSE 가 **실제로 흐르는지** 확인한다 (ADR-0044).
#
# ## 왜 별도 스크립트인가
#
# 이건 코드나 설정을 읽어서 알 수 없는 유일한 항목이다. `X-Accel-Buffering: no`
# 가 응답에 있는지는 `verify-container.sh` 스타일로 볼 수 있지만, **헤더가
# 있다는 것과 이벤트가 나눠서 도착한다는 것은 다른 명제다.** 프록시가 하나 더
# 끼거나, gzip 이 켜지거나, `proxy_http_version` 이 1.0 이면 헤더가 멀쩡해도
# 전부 끝난 뒤 한꺼번에 온다.
#
# `verify-container.sh` 에 넣지 않은 이유는 비용이다 — 그 스크립트는 이미
# 하루 50회 중 9회를 쓴다. 이건 1회를 더 쓰므로 따로 부른다.
#
# 사용:
#   DEMO_PASSWORD=... ./load/verify-sse.sh
#   WEB=https://item-exchange.duckdns.org DEMO_PASSWORD=... ./load/verify-sse.sh
#
# **`python3` 이다, `python` 이 아니다.** 우분투에는 `python` 이라는 이름이 없어서,
# `python` 을 부른 예전 검증 스크립트는 배포 대상에서 아예 못 돌았다 — 증상이
# "로그인 실패 (HTTP 200)" 이라 원인이 안 보였다. 옆 스크립트가 이미 배운 것이다.
set -uo pipefail
WEB="${WEB:-http://localhost}"
PASS=0; FAIL=0
ok()  { printf '  [PASS] %s\n' "$1"; PASS=$((PASS+1)); }
bad() { printf '  [FAIL] %s\n' "$1"; FAIL=$((FAIL+1)); }

_head="$(git -C "$(dirname "$0")/.." log --oneline -1 2>/dev/null || echo '(git 아님)')"
printf '검사 판본: %s\n\n' "$_head"

if [ -z "${DEMO_PASSWORD:-}" ]; then
  echo "DEMO_PASSWORD 가 없어 로그인할 수 없습니다."
  exit 1
fi

TOKEN="$(curl -s --max-time 10 -X POST "$WEB/api/backend/auth/login" \
  -H 'Content-Type: application/json' \
  -d "{\"tenantCode\":\"${TENANT_CODE:-nexon}\",\"username\":\"buyer_lee\",\"password\":\"${DEMO_PASSWORD}\"}" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin).get("token",""))' 2>/dev/null)"
[ -n "$TOKEN" ] || { echo "로그인 실패 — 토큰을 못 받았습니다."; exit 1; }

# **복합 질의를 쓴다.** 도구를 여러 번 부르므로 이벤트 사이에 실제 간격이 생긴다.
# FAQ 로 재면 서버가 즉답해서 버퍼링돼 있어도 "빨리 왔다"로 보인다 — 그건 검사가
# 아니라 우연이다.
QUERY='불꽃의 대검 찾아서 시세도 알려줘'
BODY_FILE="$(mktemp)"; TIMING_FILE="$(mktemp)"
trap 'rm -f "$BODY_FILE" "$TIMING_FILE"' EXIT
python3 - "$QUERY" > "$BODY_FILE".req <<'PY'
import json, sys
# 한글을 셸 인자로 넘기면 cp949 경계에서 깨진다. 파일로 쓴다.
sys.stdout.write(json.dumps({"query": sys.argv[1], "use_cache": False}, ensure_ascii=False))
PY

echo "== 스트림을 받으며 도착 시각을 잰다 =="
# 각 줄이 도착한 시각을 붙여 기록한다. `-N` 이 curl 의 출력 버퍼를 끈다 —
# **이게 없으면 curl 자신이 버퍼링해서 서버가 흘려도 한꺼번에 보인다**(검사가
# 자기 자신 때문에 틀리는 형태다).
START="$(python3 -c 'import time; print(time.time())')"
curl -sN --max-time 120 -X POST "$WEB/api/ai/assistant/stream" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  --data-binary "@$BODY_FILE.req" \
  -D "$TIMING_FILE" \
  | while IFS= read -r line; do
      printf '%s\t%s\n' "$(python3 -c 'import time; print(time.time())')" "$line"
    done > "$BODY_FILE"
rm -f "$BODY_FILE".req

python3 - "$BODY_FILE" "$TIMING_FILE" "$START" <<'PY'
import json, sys

body_path, header_path, start = sys.argv[1], sys.argv[2], float(sys.argv[3])
rows = []
for raw in open(body_path, encoding="utf-8"):
    ts, _, line = raw.partition("\t")
    if line.startswith("data: "):
        rows.append((float(ts) - start, json.loads(line[6:])))

headers = open(header_path, encoding="utf-8", errors="replace").read().lower()
fails, passes = [], []

def check(cond, good, bad):
    (passes if cond else fails).append(good if cond else bad)

# 1. 버퍼링 해제 헤더가 **최종 응답에** 남아 있는가.
check("x-accel-buffering: no" in headers,
      "X-Accel-Buffering: no 가 응답에 있다",
      "X-Accel-Buffering 헤더가 없다 — 프록시가 지웠거나 앱이 안 붙였다")

# 2. 이벤트가 여러 건 왔는가.
check(len(rows) >= 3,
      f"이벤트 {len(rows)}건 수신",
      f"이벤트가 {len(rows)}건뿐이다 — 진행 단계가 안 나온다")

if not rows:
    print("본문이 비었다 — 아래 검사는 의미가 없다")
    for line in fails: print(f"  [FAIL] {line}")
    sys.exit(1)

# 3. 마지막이 done 인가.
last = rows[-1][1]
check(last.get("type") == "done",
      "마지막 이벤트가 done 이다",
      f"마지막 이벤트가 {last.get('type')} 다: {last.get('message', '')}")

# 4. **핵심** — 첫 이벤트가 마지막보다 충분히 먼저 왔는가.
#
# 버퍼링되면 전부 같은 순간에 도착해서 간격이 0에 가깝다. 문턱을 절대 시간이
# 아니라 **전체 소요 대비 비율**로 잡는다 — 절대값은 질의·서버 속도에 따라
# 달라지지만 "첫 이벤트가 끝과 거의 동시"라는 성질은 안 달라진다.
first_at, last_at = rows[0][0], rows[-1][0]
ratio = first_at / last_at if last_at > 0 else 1.0
check(ratio < 0.5,
      f"첫 이벤트 {first_at:.2f}s / 마지막 {last_at:.2f}s (비율 {ratio:.2f}) — 흐른다",
      f"첫 이벤트 {first_at:.2f}s / 마지막 {last_at:.2f}s (비율 {ratio:.2f}) — "
      "전부 한꺼번에 왔다. 버퍼링되고 있다")

# **판정에 쓴 값을 전부 낸다.** 실패 한 줄만 내면 사람이 다시 조사해야 한다.
print("\n수신 순서:")
for at, event in rows:
    label = event.get("stage") or event.get("type")
    print(f"  {at:6.2f}s  {event['type']:8} {label}")

print()
for line in passes: print(f"  [PASS] {line}")
for line in fails: print(f"  [FAIL] {line}")
print(f"\n통과 {len(passes)} · 실패 {len(fails)}")
sys.exit(1 if fails else 0)
PY
