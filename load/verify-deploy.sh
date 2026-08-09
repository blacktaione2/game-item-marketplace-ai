#!/usr/bin/env bash
# 배포 구성 검증 — 판정 9 (ADR-0031).
#
# **로컬에서 배포 오버레이를 띄워 확인한다.** 배포 대상에서만 확인 가능한 검사를
# 만들지 않는 것이 요점이다 — ADR-0030 에서 판정을 로컬 프로세스로만 확인해
# 컨테이너에서만 존재하는 계층(헬스체크)을 놓친 전례가 있다.
#
# 사용:
#   docker compose -f docker-compose.yml -f docker-compose.deploy.yml --profile app up -d
#   ./load/verify-deploy.sh                       # web 이 80 일 때
#   WEB=http://localhost:8081 ./load/verify-deploy.sh   # 다른 포트에 게시했을 때
#
# ## localhost 를 두드리지 않는다 (ADR-0036)
#
# 처음 판본은 `curl localhost:8080` 으로 "닫혔는가"를 봤다. **호스트를 우리가
# 독점한다는 가정**이었고, 개발기와 로컬 배포 테스트에서는 참이었다.
#
# 실제 배포 대상은 **다른 프로젝트와 공유하는 박스**였고 거기서 8080(java)·
# 8000(uvicorn)·80(nginx)이 이미 사용 중이었다. 그 결과:
#
#   8080/8000 → 남의 프로세스가 응답 → "열려 있다"고 **거짓 실패**
#   80        → 남의 nginx 가 응답 → "프록시 살아 있다"고 **거짓 통과**
#
# 마지막이 특히 나쁘다 — 완전히 다른 애플리케이션을 재고 초록을 냈다.
# 사례집 12번("엉뚱한 대상")과 같은 형태다.
#
# **"우리 스택이 무엇을 게시하는가"는 포트를 두드려 알 게 아니라 Docker 에 물을
# 것이다.** 그러면 호스트에 누가 뭘 띄웠든 답이 흔들리지 않는다.
set -uo pipefail
WEB="${WEB:-http://localhost}"
PASS=0; FAIL=0
# 어느 판본이 돌고 있는지 먼저 밝힌다 — 이 저장소는 검사 스크립트가 낡은 채로
# 여러 라운드를 지나간 전례가 있고, `git pull` 없이 돌려 이미 고친 검사가
# 실패를 보고한 적도 있다. 결과를 읽기 전에 "무엇을 실행했는가"가 보여야 한다.
_head="$(git -C "$(dirname "$0")/.." log --oneline -1 2>/dev/null || echo '(git 아님)')"
_dirty="$(git -C "$(dirname "$0")/.." status --porcelain 2>/dev/null | head -1)"
printf '검사 판본: %s%s

' "$_head" "${_dirty:+  [작업 트리에 미커밋 변경 있음]}"
ok()  { printf '  [PASS] %s\n' "$1"; PASS=$((PASS+1)); }
bad() { printf '  [FAIL] %s\n' "$1"; FAIL=$((FAIL+1)); }

# 이 컨테이너가 **호스트에 게시한** 포트. `expose` 만 된 것은 나오지 않는다.
published() {
  docker inspect "$1" --format \
    '{{range $p, $conf := .NetworkSettings.Ports}}{{if $conf}}{{$p}} {{end}}{{end}}' 2>/dev/null
}

# **먼저 스택이 살아 있는지 확인한다.** 이게 없으면 "포트가 닫혔다"가
# "컨테이너가 안 떴다"와 구분되지 않는다 — 실제로 SecretGuard 가 백엔드 기동을
# 거부했을 때 8080 이 닫혀 있어서 그 검사가 **통과로 읽혔다.**
# **아래 게시-포트 검사가 보는 컨테이너를 전부 포함해야 한다.** `published()` 는
# 컨테이너가 없어도 빈 값을 내므로, 안 떠 있으면 "게시 포트 없음"으로 **통과**한다 —
# 사례집 5·11번("안 뜬 것을 닫힌 것으로 읽었다")과 같은 형태다.
# **`running` 은 `준비됨` 이 아니다.**
#
# 컨테이너는 시작하자마자 `running` 이고, 그 시점의 uvicorn 은 아직 포트를 안
# 열었다 — 실제로 `up -d` 직후 `/health` 를 물어 `Connection refused` 를 받았고,
# 그 오류는 **"아직 안 떴다" 와 "기동에 실패했다" 를 구분해주지 못한다.**
#
# compose 자신은 이걸 알고 있다: `ai` 서비스 헬스체크가 `start_period: 20s`,
# `retries: 12` 다. 그러니 검사도 그 신호를 봐야 한다 — 헬스체크가 있는
# 컨테이너는 `healthy` 가 될 때까지 기다리고, 없는 것만 `running` 으로 본다.
WAIT_SECONDS="${WAIT_SECONDS:-180}"

health_of() {  # 헬스체크가 없으면 빈 문자열
  docker inspect "$1" --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' 2>/dev/null
}

wait_ready() {  # 컨테이너 -> 0(준비됨) / 1(아님). 사유는 호출자가 찍는다
  local c="$1" waited=0 status health
  while [ "$waited" -lt "$WAIT_SECONDS" ]; do
    status="$(docker inspect "$c" --format '{{.State.Status}}' 2>/dev/null)"
    [ "$status" = "running" ] || return 1     # 없거나 죽었으면 기다릴 게 없다
    health="$(health_of "$c")"
    case "$health" in
      "")        return 0 ;;                  # 헬스체크 없음 — running 이면 최선
      healthy)   return 0 ;;
      unhealthy) return 1 ;;                  # 기다려도 안 낫는다
    esac
    sleep 3; waited=$((waited + 3))
  done
  return 1
}

echo "== 사전 확인: 스택이 준비됐는가 (running 이 아니라 healthy) =="
ALIVE=1
for c in gimp-backend gimp-ai gimp-web gimp-rabbitmq gimp-elasticsearch gimp-postgres gimp-redis; do
  H="$(health_of "$c")"
  if wait_ready "$c"; then
    H="$(health_of "$c")"
    ok "$c 준비됨${H:+ ($H)}"
  else
    S="$(docker inspect "$c" --format '{{.State.Status}}' 2>/dev/null)"
    H="$(health_of "$c")"
    # **판정에 쓴 값을 전부 낸다.** "실패" 한 줄만 내면 안 떴는지, 아직인지,
    # 죽었는지를 사람이 다시 조사해야 한다.
    bad "$c 가 status='${S:-없음}' health='${H:-없음}' — 아래 검사는 의미가 없다 (docker logs $c)"
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
# **목록을 적지 않는다.** 처음 판본은 컨테이너 여섯 개를 손으로 열거했고,
# `--profile observability` 의 `prometheus`·`grafana` 가 그 목록에서 빠져 있었다 —
# 하필 그 둘이 익명 조회가 켜진 대시보드와 기본 비밀번호를 가진 쪽이다.
# **오버레이의 열거가 샜는데 그걸 검사하는 쪽도 같은 열거였으니 못 잡았다.**
#
# 그래서 지금 도는 것을 **도커에 묻는다**. 규칙은 하나다 —
# "`gimp-` 컨테이너 중 게시 포트를 가진 것은 `gimp-web` 뿐이다".
# 서비스가 늘어도 이 검사는 저절로 따라간다.
RUNNING="$(docker ps --format '{{.Names}}' --filter 'name=gimp-' 2>/dev/null)"
if [ -z "$RUNNING" ]; then
  bad "gimp- 컨테이너가 하나도 안 보인다 — 검사가 대상에 닿지 못했다"
else
  for c in $RUNNING; do
    [ "$c" = "gimp-web" ] && continue
    P="$(published "$c")"
    [ -z "$P" ] && ok "$c 게시 포트 없음" \
                || bad "$c 가 호스트에 게시하고 있다: $P — nginx 우회 경로.
         docker-compose.deploy.yml 에 '$c' 서비스의 ports: !override [] 가 있는가"
  done
fi

echo
echo "== 대조: web 은 게시돼 있어야 한다 =="
# **이 대조가 없으면 위 검사는 스택이 안 떠도 통과한다.** 게시 포트가 하나도
# 없는 상태가 "안전"으로 읽히는 것을 막는다.
WP="$(published gimp-web)"
[ -n "$WP" ] && ok "gimp-web 게시: $WP" || bad "gimp-web 이 아무 포트도 게시하지 않았다"

echo
echo "== 대조: 그 포트가 우리 앱인가 ($WEB) =="
# 포트가 200 을 낸다고 우리 것은 아니다 — 공유 호스트에서 남의 nginx 가
# 응답해 통과한 전례가 있다. 그래서 **내용을 본다.**
BODY="$(curl -s --max-time 5 "$WEB/" 2>/dev/null)"
if printf '%s' "$BODY" | grep -q "거래소"; then
  ok "우리 앱이 응답한다 (title 확인)"
else
  bad "$WEB 이 우리 앱이 아니다 — 다른 서비스가 그 포트를 쓰고 있는가"
fi

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
    "$WEB/api/backend/auth/login" -H 'Content-Type: application/json' \
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
