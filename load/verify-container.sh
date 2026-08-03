#!/usr/bin/env bash
# 컨테이너 기동 검증 (ADR-0029).
#
# 새 판정선을 만들지 않는다 — 이미 있는 이진 기준을 재사용한다.
# 컨테이너화는 성능 작업이 아니라서 p95 같은 숫자를 걸면 도커 오버헤드를 잰다.
#
#   1. /api/assistant 의 검색·시세·이상거래·FAQ 4분기가 전부 응답하는가
#   2. 오버셀 0  (load/run.sh 가 담당 — 여기서는 안내만)
#   3. /items/3 새로고침이 404 가 아닌가 (SPA 폴백)
#   4. 두 서버가 CORS 헤더를 내지 않는가
#
# 사용: ./load/verify-container.sh [WEB_ORIGIN]
set -uo pipefail

WEB="${1:-http://localhost}"
BACKEND_DIRECT="${BACKEND_DIRECT:-http://localhost:8080}"
PASS=0
FAIL=0

ok()   { printf '  [PASS] %s\n' "$1"; PASS=$((PASS+1)); }
bad()  { printf '  [FAIL] %s\n' "$1"; FAIL=$((FAIL+1)); }

# 한글 질의는 셸 인자로 넘기면 로케일 코덱에 깨진다(cp949 경계).
# UTF-8 파일로 써서 --data-binary 로 보낸다 — docs/05-Troubleshooting 참고.
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
write_query() { python -c "
import io,json,sys
io.open(sys.argv[1],'w',encoding='utf-8').write(
    json.dumps({'query': sys.argv[2], 'use_cache': False}, ensure_ascii=False))
" "$1" "$2"; }

echo "== 로그인 =="
# ADR-0031 이 `demo-token` 을 제거했는데 **이 스크립트를 안 고쳤다.** 그래서 이
# 검사는 첫 줄에서 죽어 있었고, 배포 절차 4단계가 실행을 지시하는데도 6단계 동안
# 아무도 눈치채지 못했다 — 계약이 바뀌면 그 계약을 쓰는 검사를 다시 돌려야 한다.
GM_PW="${ADMIN_PASSWORD:?ADMIN_PASSWORD 가 필요합니다 (gm_admin 으로 로그인한다)}"
LOGIN_BODY="$(curl -s -w '\n%{http_code}' -X POST "$WEB/api/backend/auth/login" \
  -H 'Content-Type: application/json' -d "{\"username\":\"gm_admin\",\"password\":\"$GM_PW\"}")"
LOGIN_CODE="$(printf '%s' "$LOGIN_BODY" | tail -1)"
TOKEN="$(printf '%s' "$LOGIN_BODY" | sed '$d' \
  | python -c "import sys,json;print(json.load(sys.stdin).get('token',''))" 2>/dev/null)"
if [ -n "$TOKEN" ]; then
  ok "로그인 성공 (프록시 경유)"
else
  # **코드를 같이 낸다.** 처음엔 "비밀번호가 주입됐는지 확인"만 찍었는데, 실제
  # 원인은 앞서 돌린 verify-auth.sh 가 로그인 한도(30회/분)를 소진한 429 였다.
  # 원인을 잘못 지목하는 검사는 없느니만 못하다 — 사람이 엉뚱한 곳을 파게 만든다.
  case "$LOGIN_CODE" in
    429) bad "로그인 429 — 한도 소진. verify-auth.sh 직후라면 1분 기다렸다 다시 돌린다" ;;
    401) bad "로그인 401 — ADMIN_PASSWORD 가 기동 시 주입된 값과 다르다" ;;
    *)   bad "로그인 실패 (HTTP $LOGIN_CODE)" ;;
  esac
  exit 1
fi

echo
echo "== 판정 1: /api/assistant 4분기 =="
branch() {  # 이름, 질의, 기대 intent
  write_query "$TMP/q.json" "$2"
  RES="$(curl -s -X POST "$WEB/api/ai/assistant" \
    -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
    --data-binary "@$TMP/q.json")"
  GOT="$(printf '%s' "$RES" | python -c "
import sys,json
try:
    d=json.load(sys.stdin); print(d.get('intent','<없음>'), len(d.get('answer','')))
except Exception: print('<파싱실패>', 0)")"
  INTENT="${GOT%% *}"; LEN="${GOT##* }"
  if [ "$INTENT" = "$3" ] && [ "${LEN:-0}" -gt 0 ]; then
    ok "$1 (intent=$INTENT, 답변 ${LEN}자)"
  else
    bad "$1 — intent=$INTENT (기대 $3), 답변 ${LEN}자"
  fi
}
branch "FAQ"        "수수료 얼마야?"              "faq_smalltalk"
branch "검색"       "5만원 이하 검 찾아줘"        "item_search"
branch "시세"       "미스릴 단검 시세 알려줘"     "price_forecast"
branch "이상거래"   "거래 3번 이상한지 봐줘"      "anomaly_check"

echo
echo "== 판정 3: SPA 폴백 =="
CODE="$(curl -s -o /dev/null -w '%{http_code}' "$WEB/items/3")"
[ "$CODE" = "200" ] && ok "/items/3 직접 접근 = 200" || bad "/items/3 = $CODE (기대 200)"

echo
echo "== 판정 4: CORS 헤더 없음 =="
# grep 은 설정 유무만 본다. 여기서는 **실제 응답 헤더**를 본다 —
# 프레임워크 기본값처럼 소스에 안 보이는 경로까지 잡으려면 이쪽이어야 한다.
#
# **경로가 실재하는지 먼저 단언한다.** 처음 이 검사는 `/api/ai/health` 를 봤는데
# 그건 404였다(AI 서버의 헬스는 `/health` 지 `/api/health` 가 아니다). 404 에는
# 미들웨어가 헤더를 안 붙여서, CORS 를 일부러 켜고 돌려도 **통과했다.**
# 검사가 대상에 닿았는지를 확인하지 않으면 아무것도 확인하지 않는 검사가 된다.
cors_check() {  # 경로, 메서드, 본문
  local path="$1" method="$2" body="${3:-}"
  local args=(-s -D- -o /dev/null -H "Origin: http://evil.example" -X "$method")
  [ -n "$body" ] && args+=(-H 'Content-Type: application/json' -d "$body")
  local resp; resp="$(curl "${args[@]}" "$WEB$path")"
  local code; code="$(printf '%s' "$resp" | head -1 | awk '{print $2}')"
  if [ "$code" = "404" ]; then
    bad "$path — 404. 이 경로는 앱을 지나지 않아 검사가 성립하지 않는다"
    return
  fi
  local H; H="$(printf '%s' "$resp" | grep -i 'access-control' || true)"
  [ -z "$H" ] && ok "$path (HTTP $code) — CORS 헤더 없음" \
               || bad "$path (HTTP $code) — CORS 헤더 발견: $H"
}
cors_check "/api/backend/health"  GET
# 인증이 없어 401 이 나오지만 **앱을 지난 응답**이라 미들웨어가 걸린다.
cors_check "/api/ai/assistant"    POST '{}'

echo
echo "== 저장소 가드 (설정이 들어왔는지) =="
B="$(grep -rniE 'crossorigin|corsconfiguration|addcorsmappings' backend/src/main --include=*.java | wc -l)"
A="$(grep -rniE 'corsmiddleware|allow_origins' ai/app --include=*.py | wc -l)"
[ "$B" -eq 0 ] && ok "백엔드 CORS 설정 0건" || bad "백엔드 CORS 설정 ${B}건"
[ "$A" -eq 0 ] && ok "AI 서버 CORS 설정 0건" || bad "AI 서버 CORS 설정 ${A}건"

echo
echo "== 판정 2 (오버셀 0) =="
echo "  이 스크립트가 하지 않는다. 별도 실행:"
echo "    ./load/run.sh purchase contended   # 재고 감소분 == 성공 응답 수 + 워밍업"

echo
echo "----------------------------------------"
printf '통과 %d · 실패 %d\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ] || exit 1
