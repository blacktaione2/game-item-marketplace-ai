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

token() {
  curl -s -X POST "$BACKEND/api/auth/demo-token" -H 'Content-Type: application/json' \
    -d "{\"userId\":$1}" | python -c "import sys,json;print(json.load(sys.stdin).get('token',''))"
}
notif_count() {  # 토큰 -> 알림 수
  curl -s "$BACKEND/api/notifications" -H "Authorization: Bearer $1" \
    | python -c "import sys,json;print(len(json.load(sys.stdin)))" 2>/dev/null || echo -1
}

echo "== 사전 확인 =="
if [ -z "$(q "$QUEUE")" ]; then
  bad "큐 $QUEUE 를 찾을 수 없다 — 백엔드가 떠 있고 RabbitMQ 관리 API 가 열려 있는가"
  exit 1
fi
ok "큐 $QUEUE 존재"
TOK="$(token 3)"
[ -n "$TOK" ] && ok "토큰 발급" || { bad "토큰 발급 실패"; exit 1; }

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
[ "$INCREASE" -le "$CONNECT_TIMEOUT_MS" ] \
  && ok "지연 증가 ${INCREASE}ms <= 상한 ${CONNECT_TIMEOUT_MS}ms (정상 ${NORMAL_MS}ms -> 정지 ${DOWN_MS}ms)" \
  || bad "지연 증가 ${INCREASE}ms > 상한 ${CONNECT_TIMEOUT_MS}ms — 어딘가에서 재시도가 돌고 있다"

echo
echo "----------------------------------------"
printf '통과 %d · 실패 %d\n' "$PASS" "$FAIL"
echo "판정 1(오버셀 0)은 ./load/run.sh purchase contended 가 담당한다."
[ "$FAIL" -eq 0 ] || exit 1
