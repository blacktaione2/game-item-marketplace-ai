#!/usr/bin/env bash
# 공개 준비 검증 (ADR-0031).
#
#   1. /api/auth/demo-token 이 사라졌다
#   2. 틀린 비밀번호 401 / 맞으면 200
#   3. 비밀번호 분리가 **양방향**으로 성립한다
#   4. 로그인 없이 /api/assistant -> 401
#   5. 일일 한도 초과 -> 429
#   6. 신뢰하지 않는 출처의 X-Forwarded-For 는 무시된다
#
# **판정 6 은 백엔드 포트에 직접 쏜다.** nginx 를 거치면 nginx 가 헤더를 자기 값으로
# 덮으므로 스푸핑 시나리오 자체가 재현되지 않는다. 즉 이 검사는 **포트가 열린 로컬
# 구성**에서만 의미가 있다 — 배포에서는 포트를 닫으므로(판정 9, verify-deploy.sh)
# 애초에 닿지 않는다. 둘은 다른 계층이고 서로를 대체하지 않는다.
#
# 사용: DEMO_PASSWORD=... ADMIN_PASSWORD=... ./load/verify-auth.sh
set -uo pipefail

WEB="${WEB:-http://localhost}"
BACKEND_DIRECT="${BACKEND_DIRECT:-http://localhost:8080}"
AI_DIRECT="${AI_DIRECT:-http://localhost:8000}"
DEMO_PW="${DEMO_PASSWORD:?DEMO_PASSWORD 가 필요합니다}"
ADMIN_PW="${ADMIN_PASSWORD:?ADMIN_PASSWORD 가 필요합니다}"
DEMO_USERS="seller_kim buyer_lee trader_park newbie_choi"
ADMIN_USER="gm_admin"
PASS=0; FAIL=0
ok()  { printf '  [PASS] %s\n' "$1"; PASS=$((PASS+1)); }
bad() { printf '  [FAIL] %s\n' "$1"; FAIL=$((FAIL+1)); }

login_code() {  # 사용자, 비밀번호 -> HTTP 코드
  curl -s -o /dev/null -w '%{http_code}' -X POST "$WEB/api/backend/auth/login" \
    -H 'Content-Type: application/json' \
    -d "{\"username\":\"$1\",\"password\":\"$2\"}"
}
login_token() {
  curl -s -X POST "$WEB/api/backend/auth/login" -H 'Content-Type: application/json' \
    -d "{\"username\":\"$1\",\"password\":\"$2\"}" \
    | python -c "import sys,json;print(json.load(sys.stdin).get('token',''))" 2>/dev/null
}

echo "== 판정 1: demo-token 이 사라졌다 =="
TOK="$(login_token buyer_lee "$DEMO_PW")"
[ -n "$TOK" ] || { bad "로그인 자체가 안 된다 — 비밀번호가 주입됐는지 확인"; exit 1; }
# **유효한 토큰을 들고 간다.** 인증 없이 가면 보안 계층이 먼저 401 을 내는데 그건
# "핸들러가 없다"의 증거가 아니다 — 누군가 인증 뒤에 되살려도 똑같이 401 이다.
CODE="$(curl -s -o /dev/null -w '%{http_code}' -X POST "$WEB/api/backend/auth/demo-token" \
  -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' -d '{"userId":1}')"
[ "$CODE" = "404" ] && ok "demo-token 404 (토큰을 들고도 핸들러가 없다)" \
                    || bad "demo-token 이 $CODE — 아직 살아 있을 수 있다"

echo
echo "== 판정 2: 비밀번호 검증 =="
[ "$(login_code buyer_lee "$DEMO_PW")" = "200" ] && ok "올바른 비밀번호 -> 200" || bad "올바른 비밀번호가 거절됐다"
[ "$(login_code buyer_lee "틀린값")" = "401" ] && ok "틀린 비밀번호 -> 401" || bad "틀린 비밀번호가 401 이 아니다"
[ "$(login_code 없는계정 "$DEMO_PW")" = "401" ] && ok "없는 사용자도 401 (사용자 열거 차단)" || bad "없는 사용자가 401 이 아니다"

echo
echo "== 판정 3: 비밀번호 분리 — 양방향 =="
[ "$(login_code $ADMIN_USER "$DEMO_PW")" = "401" ] \
  && ok "일반 비밀번호로 GM 로그인 불가" || bad "일반 비밀번호로 GM 이 열렸다"
REVERSE_OK=1
for u in $DEMO_USERS; do
  [ "$(login_code "$u" "$ADMIN_PW")" = "401" ] || { bad "GM 비밀번호로 $u 로그인됨"; REVERSE_OK=0; }
done
# 역방향을 계정 전부에 도는 이유: 초기화가 **한 계정에만** 잘못 적용되는 것이
# 실제 실수 형태다. 하나만 보면 그걸 놓친다.
[ "$REVERSE_OK" = "1" ] && ok "GM 비밀번호로 일반 계정 로그인 불가 (4계정 전부)"
[ "$(login_code $ADMIN_USER "$ADMIN_PW")" = "200" ] && ok "GM 은 GM 비밀번호로 로그인" || bad "GM 로그인 실패"

echo
echo "== 판정 4: 로그인 없이 assistant 접근 =="
CODE="$(curl -s -o /dev/null -w '%{http_code}' -X POST "$WEB/api/ai/assistant" \
  -H 'Content-Type: application/json' -d '{"query":"x"}')"
[ "$CODE" = "401" ] && ok "토큰 없이 /api/assistant -> 401" || bad "토큰 없이 $CODE"

echo
echo "== 판정 6: 신뢰하지 않는 출처의 XFF 는 무시된다 =="
# **백엔드 포트에 직접** 쏜다. nginx 를 거치면 nginx 가 헤더를 덮어써서
# 스푸핑 자체가 재현되지 않는다.
if ! curl -s -o /dev/null --max-time 3 "$BACKEND_DIRECT/api/health"; then
  bad "백엔드 직결 포트가 닫혀 있다 — 이 검사는 포트가 열린 구성에서만 성립한다"
else
  # 위조 IP 를 매번 바꿔가며 로그인 한도를 우회할 수 있는지 본다.
  # XFF 를 신뢰하면 매 요청이 새 버킷이라 한도가 사실상 사라진다.
  LIMIT_HIT=0
  for i in $(seq 40); do
    C="$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BACKEND_DIRECT/api/auth/login" \
      -H "X-Forwarded-For: 10.0.0.$i" -H 'Content-Type: application/json' \
      -d '{"username":"buyer_lee","password":"틀린값"}')"
    [ "$C" = "429" ] && { LIMIT_HIT=1; break; }
  done
  [ "$LIMIT_HIT" = "1" ] \
    && ok "위조 XFF 로 한도를 우회하지 못한다 (429 도달)" \
    || bad "위조 XFF 40개로 한도를 넘겼다 — XFF 를 신뢰하고 있다"
fi

echo
echo "== 판정 5: 일일 한도 =="
echo "  건너뜀 — 50회 호출은 OpenAI 과금이 발생한다."
echo "  RATE_LIMIT_ASSISTANT_PER_DAY=2 로 AI 서버를 띄우고 확인할 것:"
echo "    3번째 요청이 429 여야 한다 (ai/tests/test_rate_limit.py 가 단위로 고정)"

echo
echo "----------------------------------------"
printf '통과 %d · 실패 %d\n' "$PASS" "$FAIL"
echo "판정 9(포트 미노출)는 ./load/verify-deploy.sh 가 담당한다."
[ "$FAIL" -eq 0 ] || exit 1
