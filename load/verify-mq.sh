#!/usr/bin/env bash
# 체결 후 알림 큐 종단 검증 (ADR-0030).
#
# 단위 테스트로 재현할 수 없는 것만 여기서 본다:
#   2. 부하 후 성공 구매 수 == 알림 수   (큐가 비기를 기다린 뒤)
#   5. 브로커가 죽어도 구매가 성공하고, 지연이 상한 안이다 (fail-open)
#   6. 컨슈머가 계속 실패하면 DLQ 로 간다
#   7. 재전달이 DLQ 를 채우지 않는다
#
# 사용: ./load/verify-mq.sh
set -uo pipefail

BACKEND="${BACKEND:-http://localhost:8080}"
RABBIT_API="${RABBIT_API:-http://localhost:15672/api}"
RABBIT_USER="${RABBITMQ_USER:-gimp}"
RABBIT_PASS="${RABBITMQ_PASSWORD:-gimp_local_pw}"
QUEUE="gimp.trade.completed"
DLQ="gimp.trade.completed.dlq"
# application.yml 의 spring.rabbitmq.connection-timeout 에서 나온 값이다.
# 여기 숫자만 바꾸면 근거가 사라진다 — 설정과 같이 움직여야 한다.
CONNECT_TIMEOUT_MS=2000

# **상한은 타임아웃 자체가 아니라 그 1.5배다.** 처음엔 "증가 <= 타임아웃"으로
# 걸었는데 그건 산술적으로 통과할 수 없다:
#
#   지연 증가 = (정상 + 타임아웃) - 정상 = 타임아웃 + 측정 오버헤드
#
# 오버헤드가 0 이 아닌 한 항상 초과한다(실측 2,002ms / 2,004ms vs 상한 2,000ms).
# 결과를 보고 낮춘 게 아니라 **도출이 틀린 것을 고쳤다**(ADR-0030 정정 참고).
#
# **이 검사가 무엇을 못 하는지도 적어둔다.** 처음엔 "재시도가 도는가를 가린다"고
# 적었는데 **틀렸다** — `template.retry.enabled: true` 로 바꿔 실측했더니 지연이
# 1,997ms 로 그대로였다(재시도가 연결 실패에는 걸리지 않는다). 이 검사가 보장하는
# 것은 **fail-open 의 지연이 타임아웃 1회분을 크게 넘지 않는다**는 것뿐이고,
# 재시도 설정의 회귀는 잡지 못한다.
LATENCY_CAP_MS=$(( CONNECT_TIMEOUT_MS * 3 / 2 ))
PASS=0; FAIL=0
ok()  { printf '  [PASS] %s\n' "$1"; PASS=$((PASS+1)); }
bad() { printf '  [FAIL] %s\n' "$1"; FAIL=$((FAIL+1)); }

q() {  # 큐 이름 -> "ready unacked"  (없으면 빈 문자열)
  curl -su "$RABBIT_USER:$RABBIT_PASS" "$RABBIT_API/queues/%2F/$1" 2>/dev/null \
    | python -c "
import sys,json
try:
    d=json.load(sys.stdin)
    print(d.get('messages_ready',0), d.get('messages_unacknowledged',0))
except Exception:
    pass"
}

# **큐가 빌 때까지 기다린다.** ready 만 보면 컨슈머가 물고 있는 것을 놓친다 —
# unacked 까지 0이어야 처리가 끝난 것이다.
#
# **시간 초과는 통과가 아니라 실패다.** 안 비면 그게 신호다. 이 저장소에는
# 대기 조건이 '대상 없음'을 '조건 충족'으로 읽어 조용히 통과한 전례가 있다.
drain() {
  local deadline=$(( $(date +%s) + ${1:-30} ))
  while [ "$(date +%s)" -lt "$deadline" ]; do
    local d; d="$(q "$QUEUE")"
    [ "$d" = "0 0" ] && return 0
    sleep 1
  done
  return 1
}

# ADR-0031 이 `demo-token` 을 없애면서 이 함수가 죽었다 — 인자가 userId 였는데
# 이제는 username + 비밀번호다. 스크립트가 재실행되지 않아 계약 변경이 드러나지
# 않았고, 실행하면 404 본문을 JSON 으로 파싱하다 traceback 이 났다.
DEMO_PW="${DEMO_PASSWORD:?DEMO_PASSWORD 가 필요합니다}"
# 자격증명은 테넌트 + 아이디 + 비밀번호 셋이다 (ADR-0034).
TENANT_CODE="${TENANT_CODE:-nexon}"
token() {  # username -> JWT
  curl -s -X POST "$BACKEND/api/auth/login" -H 'Content-Type: application/json' \
    -d "{\"tenantCode\":\"$TENANT_CODE\",\"username\":\"$1\",\"password\":\"$DEMO_PW\"}" \
    | python -c "import sys,json;print(json.load(sys.stdin).get('token',''))" 2>/dev/null
}
# **/api/notifications 의 길이를 세면 안 된다.** 그 엔드포인트는 최근 20건 상한이라
# 20건이 쌓인 뒤로는 몇 건을 더 만들어도 길이가 20 -> 20 이다. 실제로 그걸 세다가
# "구매 5건인데 알림 0건"이라는 거짓 실패를 두 번 봤고, 계측을 확인하고서야
# (발행 ok=99 / 큐 consumers=1) 시스템이 아니라 검사가 틀렸음을 알았다.
#
# unread-count 는 상한이 없는 진짜 집계다. 이 흐름에서 알림을 읽음 처리하지
# 않으므로 총계와 같다.
notif_count() {  # 토큰 -> 알림 수 (상한 없음)
  curl -s "$BACKEND/api/notifications/unread-count" -H "Authorization: Bearer $1" \
    | python -c "import sys,json;print(json.load(sys.stdin)['count'])" 2>/dev/null || echo -1
}

echo "== 사전 확인 =="
if [ -z "$(q "$QUEUE")" ]; then
  bad "큐 $QUEUE 를 찾을 수 없다 — 백엔드가 떠 있고 RabbitMQ 관리 API 가 열려 있는가"
  exit 1
fi
ok "큐 $QUEUE 존재"
TOK="$(token buyer_lee)"   # 예전 userId 3
if [ -n "$TOK" ]; then
  ok "로그인"
else
  # 코드를 같이 낸다 — 429(한도 소진)와 401(비밀번호 불일치)은 처방이 정반대다.
  LC="$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BACKEND/api/auth/login" \
    -H 'Content-Type: application/json' \
    -d "{\"tenantCode\":\"$TENANT_CODE\",\"username\":\"buyer_lee\",\"password\":\"$DEMO_PW\"}")"
  case "$LC" in
    429) bad "로그인 429 — 한도 소진. 다른 검사 직후라면 1분 기다렸다 다시 돌린다" ;;
    401) bad "로그인 401 — DEMO_PASSWORD 가 기동 시 주입된 값과 다르다" ;;
    *)   bad "로그인 실패 (HTTP $LC)" ;;
  esac
  exit 1
fi

echo
echo "== 판정 2: 구매 수 == 알림 수 (큐 배수 후) =="
BEFORE="$(notif_count "$TOK")"
N=5
CREATED=0
for _ in $(seq $N); do
  CODE="$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BACKEND/api/items/9001/purchase" \
    -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' -d '{"quantity":1}')"
  [ "$CODE" = "201" ] && CREATED=$((CREATED+1))
done
if drain 30; then
  ok "큐 배수 완료 (ready=0, unacked=0)"
  AFTER="$(notif_count "$TOK")"
  DELTA=$((AFTER - BEFORE))
  [ "$DELTA" = "$CREATED" ] \
    && ok "구매 $CREATED 건 -> 구매자 알림 $DELTA 건" \
    || bad "구매 $CREATED 건인데 알림 $DELTA 건"
else
  bad "30초 안에 큐가 비지 않았다 — 컨슈머가 멈췄거나 실패 루프일 수 있다"
fi

echo
echo "== 판정 7: 재전달이 DLQ 를 채우지 않는다 =="
DLQ_DEPTH="$(q "$DLQ")"
[ "$DLQ_DEPTH" = "0 0" ] \
  && ok "DLQ 비어 있음 ($DLQ)" \
  || bad "DLQ 에 메시지가 있다: $DLQ_DEPTH — 멱등 처리가 실패로 다뤄지고 있을 수 있다"

echo
echo "== 판정 5: 브로커 정지 시 fail-open =="
echo "  정상 상태 응답 시간 측정..."
NORMAL_MS="$(curl -s -o /dev/null -w '%{time_total}' -X POST "$BACKEND/api/items/9001/purchase" \
  -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' -d '{"quantity":1}' \
  | python -c "import sys;print(int(float(sys.stdin.read())*1000))")"
echo "  브로커 정지..."
docker stop gimp-rabbitmq >/dev/null 2>&1
sleep 2
RESULT="$(curl -s -o /dev/null -w '%{http_code} %{time_total}' -X POST "$BACKEND/api/items/9001/purchase" \
  -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' -d '{"quantity":1}')"
DOWN_CODE="${RESULT%% *}"
DOWN_MS="$(printf '%s' "${RESULT##* }" | python -c "import sys;print(int(float(sys.stdin.read())*1000))")"
echo "  브로커 재기동..."
docker start gimp-rabbitmq >/dev/null 2>&1

[ "$DOWN_CODE" = "201" ] \
  && ok "브로커 정지 상태에서도 구매 성공 (201)" \
  || bad "브로커 정지 시 구매가 $DOWN_CODE — fail-open 이 아니다"

INCREASE=$((DOWN_MS - NORMAL_MS))
[ "$INCREASE" -le "$LATENCY_CAP_MS" ] \
  && ok "지연 증가 ${INCREASE}ms <= 상한 ${LATENCY_CAP_MS}ms (타임아웃 ${CONNECT_TIMEOUT_MS}ms x1.5). 정상 ${NORMAL_MS}ms -> 정지 ${DOWN_MS}ms" \
  || bad "지연 증가 ${INCREASE}ms > 상한 ${LATENCY_CAP_MS}ms — 재시도가 돌고 있다 (2배 이상이면 재시도다)"

echo
echo "== 판정 9: 브로커가 돌아오면 발행이 재개된다 =="
# **이 검사가 없으면 이 스크립트를 연달아 두 번 못 돌린다.** 판정 5가 브로커를
# 정지시켰다 살리는데, 백엔드의 캐시된 연결은 곧바로 복구되지 않는다. 그 상태로
# 다음 실행의 판정 2가 돌면 "구매 5건인데 알림 0건"이 나온다 — 시스템이 아니라
# 실행 순서가 만든 거짓 실패다(실제로 한 번 겪었다).
#
# 그래서 끝에서 복구를 **기다리고 확인한다.** 덤으로 "브로커가 돌아오면 알림이
# 다시 만들어진다"는 진짜 성질을 단언하게 된다.
RECOVERED=0
BEFORE_R="$(notif_count "$TOK")"
for _ in $(seq 30); do
  curl -s -o /dev/null -X POST "$BACKEND/api/items/9001/purchase" \
    -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' -d '{"quantity":1}'
  sleep 2
  [ "$(notif_count "$TOK")" -gt "$BEFORE_R" ] && { RECOVERED=1; break; }
done
[ "$RECOVERED" = "1" ] \
  && ok "브로커 복구 후 알림 생성 재개" \
  || bad "60초 안에 발행이 재개되지 않았다 — 연결 복구가 안 되고 있다"

echo
echo "----------------------------------------"
printf '통과 %d · 실패 %d\n' "$PASS" "$FAIL"
echo "판정 1(오버셀 0)은 ./load/run.sh purchase contended 가 담당한다."
[ "$FAIL" -eq 0 ] || exit 1
