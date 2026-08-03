#!/usr/bin/env bash
# 배포 구성 검증 — 판정 9 (ADR-0031).
#
# **로컬에서 배포 오버레이를 띄워 확인한다.** 배포 대상에서만 확인 가능한 검사를
# 만들지 않는 것이 요점이다 — ADR-0030 에서 판정을 로컬 프로세스로만 확인해
# 컨테이너에서만 존재하는 계층(헬스체크)을 놓친 전례가 있다.
#
# 사용:
#   docker compose -f docker-compose.yml -f docker-compose.deploy.yml --profile app up -d
#   ./load/verify-deploy.sh
set -uo pipefail
PASS=0; FAIL=0
ok()  { printf '  [PASS] %s\n' "$1"; PASS=$((PASS+1)); }
bad() { printf '  [FAIL] %s\n' "$1"; FAIL=$((FAIL+1)); }

closed() {  # 포트가 닫혀 있으면 0
  ! curl -s -o /dev/null --max-time 3 "http://localhost:$1/" 2>/dev/null
}

# **먼저 스택이 살아 있는지 확인한다.** 이게 없으면 "포트가 닫혔다"가
# "컨테이너가 안 떴다"와 구분되지 않는다 — 실제로 SecretGuard 가 백엔드 기동을
# 거부했을 때 8080 이 닫혀 있어서 그 검사가 **통과로 읽혔다.**
echo "== 사전 확인: 스택이 실제로 떠 있는가 =="
ALIVE=1
for c in gimp-backend gimp-ai gimp-web; do
  S="$(docker inspect "$c" --format '{{.State.Status}}' 2>/dev/null)"
  if [ "$S" = "running" ]; then
    ok "$c 실행 중"
  else
    bad "$c 가 '$S' — 아래 포트 검사는 의미가 없다 (docker logs $c 를 볼 것)"
    ALIVE=0
  fi
done
if [ "$ALIVE" != "1" ]; then
  echo
  echo "----------------------------------------"
  printf '통과 %d · 실패 %d\n' "$PASS" "$FAIL"
  echo "스택이 안 떠 있어 포트 검사를 건너뛴다."
  exit 1
fi

echo
echo "== 판정 9: nginx 만 외부에 노출된다 =="
for spec in "8080 backend" "8000 ai" "15672 rabbitmq-관리" "9200 elasticsearch"; do
  port="${spec%% *}"; name="${spec##* }"
  closed "$port" && ok "$name ($port) 닫힘" || bad "$name ($port) 가 열려 있다 - nginx 우회 경로"
done

echo
echo "== 대조: nginx 는 열려 있어야 한다 =="
curl -s -o /dev/null --max-time 5 "http://localhost/" \
  && ok "nginx (80) 응답 - 프록시 경로는 살아 있다" \
  || bad "nginx 가 응답하지 않는다"

echo
echo "== prod 프로파일이 걸렸는가 =="
P="$(docker exec gimp-backend sh -c 'echo $SPRING_PROFILES_ACTIVE' 2>/dev/null)"
[ "$P" = "prod" ] && ok "SPRING_PROFILES_ACTIVE=prod (SecretGuard 활성)" \
                  || bad "프로파일이 '$P' - SecretGuard 가 동작하지 않는다"

echo
echo "== 실제로 쓸 수 있는가 (프록시 경유) =="
# **포트가 닫혔다는 것만으로는 부족하다.** 스택이 떠 있어도 로그인이 안 되면
# 배포가 아니다. 이 검사 하나가 두 가지 실수를 동시에 잡는다:
#   401 -> 비밀번호 초기화가 db-seed 에 덮였다 (기동 후 restart 를 안 했다)
#   502 -> nginx 가 옛 IP 를 물고 있다 (컨테이너 재생성 후 restart web 필요)
if [ -z "${DEMO_PASSWORD:-}" ]; then
  bad "DEMO_PASSWORD 가 없어 로그인 확인을 못 한다 — 이 검사 없이는 배포를 신뢰할 수 없다"
else
  CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 -X POST \
    "http://localhost/api/backend/auth/login" -H 'Content-Type: application/json' \
    -d "{\"tenantCode\":\"${TENANT_CODE:-nexon}\",\"username\":\"buyer_lee\",\"password\":\"${DEMO_PASSWORD}\"}")"
  case "$CODE" in
    200) ok "프록시 경유 로그인 200" ;;
    400) bad "로그인 400 — 요청이 불완전하다. tenantCode 가 빠졌는가 (ADR-0034)" ;;
    401) bad "로그인 401 — 비밀번호가 안 걸렸다. 기동 후 'restart backend web' 을 했는가" ;;
    502) bad "로그인 502 — nginx 가 옛 IP 를 물고 있다. 'restart web' 이 필요하다" ;;
    *)   bad "로그인이 $CODE" ;;
  esac
fi

echo
echo "----------------------------------------"
printf '통과 %d · 실패 %d\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ] || exit 1
