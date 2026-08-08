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

# **프록시를 거쳐 백엔드가 응답할 때까지 먼저 기다린다.**
#
# `curl $WEB/` 로 기다리면 안 된다 — nginx 는 백엔드가 죽어 있어도 **정적 SPA 를
# 그대로 내주므로** 즉시 통과하고, 그 뒤 로그인이 502 로 죽는다. 실제로 그렇게
# 막혔다(사례 27). `/api/backend/health` 는 프록시 → 백엔드를 실제로 거친다.
#
# **로그인 경로로 폴링하지 않는다** — 그건 IP 단위 30회/분이라 대기가 곧 한도
# 소진이다. `/api/health` 는 인증도 한도도 없고, AI 가 죽어 있어도 200 을 낸다
# (본문의 `aiStatus` 로만 표시). 즉 **백엔드 도달 여부만** 보는 신호다.
READY_PATH="$WEB/api/backend/health"
WAITED=0; LIMIT="${WAIT_SECONDS:-90}"
while :; do
  CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$READY_PATH")"
  [ "$CODE" = "200" ] && break
  [ "$WAITED" -ge "$LIMIT" ] && break
  sleep 3; WAITED=$((WAITED + 3))
done
if [ "$CODE" != "200" ]; then
  echo "프록시 경유 백엔드가 ${WAITED}초 동안 200 을 내지 않았다 (마지막 $CODE)"
  case "$CODE" in
    000) echo "  연결 자체가 안 된다 — 주소($WEB)와 nginx 기동을 볼 것" ;;
    502|503|504) echo "  nginx → backend 가 안 붙는다. 둘 중 하나다:"
                 echo "    (a) 백엔드가 기동 실패 →  docker logs --tail 50 gimp-backend"
                 echo "    (b) nginx 가 옛 IP 를 물었다 →  \$DC restart web" ;;
  esac
  exit 1
fi
[ "$WAITED" -gt 0 ] && echo "백엔드 준비됨 (${WAITED}초 대기)"

# **본문을 파싱하기 전에 상태 코드를 본다.** 첫 판본은 curl 을 바로 python 에
# 물려서 실패하면 `로그인 실패 — 토큰을 못 받았습니다` 한 줄만 냈다. 그 한 줄은
# nginx 가 아직 재시작 중인 것 / 비밀번호가 틀린 것 / IP 한도에 걸린 것을
# **구분해주지 않는다** — 실제로 그 상태로 사람을 막았다. 옆의
# `verify-deploy.sh` 는 이미 코드별로 진단을 내놓는다.
LOGIN_BODY="$(mktemp)"
CODE="$(curl -s -o "$LOGIN_BODY" -w '%{http_code}' --max-time 10 \
  -X POST "$WEB/api/backend/auth/login" \
  -H 'Content-Type: application/json' \
  -d "{\"tenantCode\":\"${TENANT_CODE:-nexon}\",\"username\":\"buyer_lee\",\"password\":\"${DEMO_PASSWORD}\"}")"

case "$CODE" in
  200) ;;
  000) echo "연결 자체가 안 됐다 ($WEB) — nginx 가 아직 재시작 중인지, 주소가 맞는지 볼 것"
       echo "  대기: until curl -sf $WEB/ >/dev/null; do sleep 2; done"
       exit 1 ;;
  401) echo "로그인 401 — DEMO_PASSWORD 가 틀렸다. **서버의 .env 값**을 쓸 것:"
       echo "  export DEMO_PASSWORD=\$(grep '^DEMO_PASSWORD=' .env | cut -d= -f2)"
       echo "  (기동 후 'restart backend' 을 안 했으면 db-seed 가 비밀번호를 덮었을 수도 있다 — ADR-0031)"
       exit 1 ;;
  400) echo "로그인 400 — 요청이 불완전하다. tenantCode 가 빠졌는가 (ADR-0034)" ; exit 1 ;;
  429) echo "로그인 429 — IP 단위 30회/분에 걸렸다. **1분 기다렸다 다시** 돌릴 것"
       echo "  (verify-auth.sh 를 방금 돌렸으면 그게 40회를 썼다)"
       exit 1 ;;
  502|503|504) echo "로그인 $CODE — nginx 가 백엔드 옛 IP 를 물고 있다. '\$DC restart web' 이 필요하다"
       exit 1 ;;
  *)   echo "로그인이 $CODE"; echo "본문: $(head -c 300 "$LOGIN_BODY")"; exit 1 ;;
esac

TOKEN="$(python3 -c 'import sys,json; print(json.load(sys.stdin).get("token",""))' \
  < "$LOGIN_BODY" 2>/dev/null)"
rm -f "$LOGIN_BODY"
# 200 인데 토큰이 없는 경우 — 응답 모양이 바뀐 것이다. 위와 다른 진단이다.
[ -n "$TOKEN" ] || { echo "로그인 200 인데 응답에 token 이 없다 — 응답 형태가 바뀌었는가"; exit 1; }

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
