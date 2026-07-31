// 시나리오 A — 고경합 구매. 백엔드만 때린다(외부 의존 없음).
//
// ## 이 시나리오가 답하는 것
//
// 1. **오버셀이 일어나는가.** ADR-0001은 "Redis 분산 락 + 낙관적 락이 오버셀을
//    막는다"고 주장하는데 실동시성에서 검증된 적이 없다. 성공(201) 수와 재고
//    감소분이 정확히 같아야 한다 — 이건 자의적 임계값이 아니라 이진 사실이다.
// 2. **병목이 락 대기인가 보유인가.** 백엔드가 둘을 따로 계측한다(ADR-0019).
//
// ## 두 프로파일을 대조하는 이유
//
//   contended : 전 VU가 아이템 하나(9001)에 몰린다 → 대기가 나타나야 한다
//   spread    : 20개(9002~9021)로 분산       → 대기가 사라져야 한다
//
// 대조군이 없으면 "대기 0.7초"가 큰 값인지 알 수 없다. spread에서도 대기가
// 남는다면 그건 경합이 아니라 Redisson/Redis 자체 비용이다.
//
// ## 판독 규칙 (측정 전에 정해둔다 — 사후 해석을 막으려고)
//
//   대기↑ 보유 평평  → 병목은 큐잉. 처리량 상한 ≈ 1 / 보유시간
//   대기↑ 보유도 ↑   → 병목은 트랜잭션(DB). 락은 증상이지 원인이 아니다
//   spread에서도 대기 → Redisson 클라이언트 또는 Redis
//
// 실행:
//   k6 run -e PROFILE=contended load/k6/purchase-contention.js
//   k6 run -e PROFILE=spread    load/k6/purchase-contention.js
//   k6 run -e PROFILE=contended -e STAGES=step load/k6/purchase-contention.js   # knee 탐색

import http from "k6/http";
import { check } from "k6";
import { Counter, Trend } from "k6/metrics";

const BASE = __ENV.BASE_URL || "http://localhost:8080";
const PROFILE = __ENV.PROFILE || "contended";
const VUS = Number(__ENV.VUS || 20);

// 부하 전용 아이템. seed-loadtest.sql 이 적재한다(재고 1천만).
// 데모 아이템(stock=10)을 쓰면 10요청 만에 재고가 말라 락이 아니라 거절을 잰다.
const CONTENDED_ITEM = 9001;
const SPREAD_ITEMS = Array.from({ length: 20 }, (_, i) => 9002 + i);

// 구매자는 3~5번. 2번은 판매자라 "본인 아이템" 규칙에 걸린다.
const BUYERS = [3, 4, 5];

const rejected = new Counter("rejected_409");
const created = new Counter("created_201");
const serverError = new Counter("server_5xx");
const okDuration = new Trend("purchase_ok_ms", true);

const STEP_STAGES = [
  { duration: "20s", target: 5 },
  { duration: "20s", target: 10 },
  { duration: "20s", target: 20 },
  { duration: "20s", target: 40 },
  { duration: "20s", target: 80 },
];

export const options =
  __ENV.STAGES === "step"
    ? { stages: STEP_STAGES, thresholds: thresholds() }
    : { vus: VUS, duration: __ENV.DURATION || "30s", thresholds: thresholds() };

// **지연시간 목표를 걸지 않는다.** 이 시스템의 정상 범위를 아직 모르고, 근거
// 없는 숫자를 임계값으로 박으면 이 프로젝트가 두 번 겪은 실패를 반복한다
// (오토인코더 학습셋 편향, 시맨틱 캐시 유사도). 대신 **자의적이지 않은 이진
// 기준**만 건다.
function thresholds() {
  return {
    server_5xx: ["count==0"],
    checks: ["rate==1.0"],
  };
}

function itemFor(iteration) {
  if (PROFILE === "spread") {
    return SPREAD_ITEMS[iteration % SPREAD_ITEMS.length];
  }
  return CONTENDED_ITEM;
}

export function setup() {
  // 구매자별 토큰을 미리 받아둔다. 인증이 들어오면서(ADR-0023) 헤더로 사용자를
  // 자칭할 수 없게 됐다 — VU마다 구매자가 다르므로 토큰도 사용자 수만큼 필요하다.
  // 발급을 setup에서 한 번만 하는 이유는 **측정 구간에 발급 왕복을 섞지 않기**
  // 위해서다. 토큰 TTL이 1시간이라 부하 구간(최대 수 분) 안에서는 만료되지 않는다.
  const tokens = {};
  for (const userId of BUYERS) {
    const issued = http.post(
      `${BASE}/api/auth/demo-token`,
      JSON.stringify({ userId }),
      { headers: { "Content-Type": "application/json" } },
    );
    if (issued.status !== 200) {
      throw new Error(
        `토큰 발급 실패 (user=${userId}, status=${issued.status}). ` +
          `백엔드가 떠 있고 seed-demo.sql이 적용됐는지 확인하세요.`,
      );
    }
    tokens[userId] = issued.json("token");
  }

  // 워밍업. 백엔드는 AI만큼 극적이진 않지만 JIT·커넥션 풀·Hibernate 첫 쿼리가
  // 첫 요청에 실린다. 이 값을 버리지 않고 따로 보고한다 — ADR-0019에서 AI의
  // 콜드 스타트가 35초였고, 그건 온디맨드 기동 설계에 직접 영향을 준다.
  const started = Date.now();
  const warm = http.post(
    `${BASE}/api/items/${CONTENDED_ITEM}/purchase`,
    JSON.stringify({ quantity: 1 }),
    { headers: headers(tokens[3]) },
  );
  const coldMs = Date.now() - started;
  console.log(`[warmup] status=${warm.status} cold_start_ms=${coldMs}`);
  return { coldMs, tokens };
}

function headers(token) {
  return {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
  };
}

export default function (data) {
  const itemId = itemFor(__ITER);
  const buyer = BUYERS[(__VU + __ITER) % BUYERS.length];

  const res = http.post(
    `${BASE}/api/items/${itemId}/purchase`,
    JSON.stringify({ quantity: 1 }),
    { headers: headers(data.tokens[buyer]), tags: { profile: PROFILE } },
  );

  if (res.status === 201) {
    created.add(1);
    okDuration.add(res.timings.duration);
  } else if (res.status === 409) {
    // 409 자체는 정상 동작이다. 다만 재고를 충분히 준 상태에서 나오면
    // 시나리오가 오염된 것이므로 사유를 백엔드 메트릭에서 확인해야 한다.
    rejected.add(1);
  } else if (res.status >= 500) {
    serverError.add(1);
  }

  // 201과 409만 정상이다. 그 외(특히 5xx, 400)는 결함이다.
  check(res, {
    "201 또는 409": (r) => r.status === 201 || r.status === 409,
  });
}

export function teardown(data) {
  console.log(`[teardown] profile=${PROFILE} cold_start_ms=${data.coldMs}`);
}
