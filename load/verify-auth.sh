#!/usr/bin/env bash
# 공개 준비 검증 (ADR-0031).
#
#   1. /api/auth/demo-token 이 사라졌다
#   2. 틀린 비밀번호 401 / 맞으면 200
#   3. 비밀번호 분리가 **양방향**으로 성립한다
#   4. 로그인 없이 /api/assistant -> 401
#   5. 일일 한도 초과 -> 429
#   6. 위조 X-Forwarded-For 로 로그인 한도를 우회할 수 없다 (**프록시 경유**)
#   7. 인증 없이 닿는 AI 경로가 없다
#
# **판정 6 은 프록시를 경유한다 — 그게 배포되는 경로이기 때문이다.**
# 예전 판정 6 은 백엔드 포트에 직접 쏘면서 "nginx 가 헤더를 덮으므로 프록시로는
# 재현이 안 된다"고 적어뒀는데, **그 주장이 사실이 아니었다.** nginx.conf 에
# X-Forwarded-For 를 설정하는 줄이 아예 없어서 클라이언트가 보낸 값이 그대로
# 통과했고, 런북대로 TRUSTED_PROXIES 를 채운 배포에서는 위조 40회가 전부
# 통과했다(대조군: 헤더 없이는 31번째 429). 검사는 **배포되지 않는 경로를**
# 검증하면서, 검증하지 않는 경로에 대한 미확인 주장을 근거로 달고 있었다.
# ADR-0033 에서 nginx 에 그 줄을 넣고 검사를 프록시 경유로 옮겼다.
#
# 사용: DEMO_PASSWORD=... ADMIN_PASSWORD=... ./load/verify-auth.sh
set -uo pipefail

WEB="${WEB:-http://localhost}"
AI_DIRECT="${AI_DIRECT:-http://localhost:8000}"
# **`python` 은 리눅스에 없다.** Windows(python.org)에만 그 이름이 있고 우분투는
# `python3` 뿐이라, 이 스크립트들은 **배포 대상에서 처음부터 못 돌았다.**
# 증상이 고약했다 — 로그인은 200 인데 토큰 추출만 실패해서 "비밀번호를 확인하라"고
# 나왔다. 실제로 실배포에서 그렇게 헤맸다.
#
# **이름으로 고르면 안 된다.** Windows 에는 Microsoft Store 스텁 `python3` 이 있어서
# `command -v` 는 찾지만 실행하면 "Python" 만 찍고 죽는다(exit 49). 그래서 후보마다
# **빈 프로그램을 실제로 돌려보고** 고른다 — 이 저장소가 반복해서 배운 것과 같다
# ("포트가 응답한다" ≠ "내가 띄운 프로세스다", `docker --version` ≠ 데몬 접근 가능).
if [ -z "${PY:-}" ]; then
  for _c in python3 python; do
    command -v "$_c" >/dev/null 2>&1 && "$_c" -c '' >/dev/null 2>&1 && { PY="$_c"; break; }
  done
fi
[ -n "${PY:-}" ] || { echo "동작하는 python3/python 이 필요합니다" >&2; exit 1; }
DEMO_PW="${DEMO_PASSWORD:?DEMO_PASSWORD 가 필요합니다}"
ADMIN_PW="${ADMIN_PASSWORD:?ADMIN_PASSWORD 가 필요합니다}"
DEMO_USERS="seller_kim buyer_lee trader_park newbie_choi"
ADMIN_USER="gm_admin"
# 자격증명은 테넌트 + 아이디 + 비밀번호 셋이다 (ADR-0034).
TENANT_CODE="${TENANT_CODE:-nexon}"
PASS=0; FAIL=0
ok()  { printf '  [PASS] %s\n' "$1"; PASS=$((PASS+1)); }
bad() { printf '  [FAIL] %s\n' "$1"; FAIL=$((FAIL+1)); }

login_code() {  # 사용자, 비밀번호, [테넌트] -> HTTP 코드
  curl -s -o /dev/null -w '%{http_code}' -X POST "$WEB/api/backend/auth/login" \
    -H 'Content-Type: application/json' \
    -d "{\"tenantCode\":\"${3:-$TENANT_CODE}\",\"username\":\"$1\",\"password\":\"$2\"}"
}
login_token() {
  curl -s -X POST "$WEB/api/backend/auth/login" -H 'Content-Type: application/json' \
    -d "{\"tenantCode\":\"$TENANT_CODE\",\"username\":\"$1\",\"password\":\"$2\"}" \
    | "$PY" -c "import sys,json;print(json.load(sys.stdin).get('token',''))" 2>/dev/null
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
# **본문 값은 ASCII 로 둔다.** 예전에는 "틀린값"·"없는계정" 이었는데, Windows 셸에서
# 한글 인자가 cp949 로 재인코딩돼 **깨진 JSON** 이 나갔다. 그때 서버는 400 을 냈지만
# `/error` 가 보안 체인에 걸려 **401 로 가려졌고**(ADR-0034), 그래서 이 두 검사가
# 통과했다 — 비밀번호 검증을 잰 적이 없는데 초록이었다.
#
# F1(=/error 허용)을 고치자 400 이 드러나며 즉시 실패했다. **검사가 틀린 이유로
# 통과하고 있었다는 것을 다른 수정이 알려준 사례다.**
[ "$(login_code buyer_lee "$DEMO_PW")" = "200" ] && ok "올바른 비밀번호 -> 200" || bad "올바른 비밀번호가 거절됐다"
[ "$(login_code buyer_lee "wrong-password")" = "401" ] && ok "틀린 비밀번호 -> 401" || bad "틀린 비밀번호가 401 이 아니다"
[ "$(login_code no_such_user "$DEMO_PW")" = "401" ] && ok "없는 사용자도 401 (사용자 열거 차단)" || bad "없는 사용자가 401 이 아니다"
[ "$(login_code no_such_tenant_xyz "$DEMO_PW" "no_such_tenant_xyz")" = "401" ] \
  && ok "없는 테넌트도 401 (테넌트 열거 차단)" || bad "없는 테넌트가 401 이 아니다"

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
echo "== 판정 6: 위조 XFF 로 로그인 한도를 우회할 수 없다 (프록시 경유) =="
# 위조 IP 를 매번 바꿔가며 한도를 우회할 수 있는지 본다. 헤더를 신뢰해 버리면
# 매 요청이 새 버킷이라 한도가 사실상 사라진다.
#
# **프록시를 거쳐 쏜다.** 배포에서 실제로 열려 있는 경로가 여기뿐이고, 앞선
# 버전은 백엔드 직결로 재는 바람에 프록시 경유의 구멍을 6단계 동안 못 봤다.
#
# 앞 판정들이 이미 로그인을 몇 번 썼지만 이 검사는 영향받지 않는다 — 소진은
# 언제나 429 도달을 **쉽게** 만들 뿐이고, 헤더가 신뢰되면 위조값마다 버킷이
# 새로 생기므로 소진과 무관하게 40회가 통과한다.
XFF_LIMIT_HIT=0
for i in $(seq 40); do
  C="$(curl -s -o /dev/null -w '%{http_code}' -X POST "$WEB/api/backend/auth/login" \
    -H "X-Forwarded-For: 10.9.9.$i" -H 'Content-Type: application/json' \
    -d "{\"tenantCode\":\"$TENANT_CODE\",\"username\":\"buyer_lee\",\"password\":\"wrong\"}")"
  [ "$C" = "429" ] && { XFF_LIMIT_HIT=1; break; }
done
[ "$XFF_LIMIT_HIT" = "1" ] \
  && ok "위조 XFF 40개로도 한도를 못 넘는다 (429 도달)" \
  || bad "위조 XFF 40개가 전부 통과했다 — nginx 가 XFF 를 덮어쓰지 않는다"

echo
echo "== 판정 7: 인증 없이 닿는 AI 경로가 없다 =="
# `/api/llm/test` 가 인증 없이 열려 있었다. 다른 라우터가 전부 require_actor 를
# 달 때 여기만 빠졌고, 토큰 없이 임의 프롬프트를 OpenAI 로 보낼 수 있었다 —
# 비용 방어 3계층을 통째로 우회한다(ADR-0033).
#
# **제거된 경로 하나만 확인하지 않는다.** 원인은 그 파일이 아니라 "경로마다
# 개별로 붙이면 빠뜨려도 조용하다"는 방식이라, 다음 누락도 같은 모양이다.
AI_OPEN=""
for p in "llm/test:POST" "assistant:POST" "search:POST" "forecast:POST" "anomaly/detect:POST" "anomaly/alerts:GET"; do
  path="${p%%:*}"; method="${p##*:}"
  C="$(curl -s -o /dev/null -w '%{http_code}' -X "$method" "$WEB/api/ai/$path" \
    -H 'Content-Type: application/json' -d '{}' --max-time 30)"
  # 401 이면 막힌 것, 404 면 경로 자체가 없는 것 — 둘 다 통과다.
  case "$C" in 401|404) ;; *) AI_OPEN="$AI_OPEN $path($C)";; esac
done
[ -z "$AI_OPEN" ] \
  && ok "AI 경로 6종 전부 토큰 없이는 401/404" \
  || bad "토큰 없이 응답한 AI 경로가 있다:$AI_OPEN"

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
