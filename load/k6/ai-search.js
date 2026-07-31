// 시나리오 B — AI 검색. 두 모드로 나눠 **OpenAI 몫을 분리**한다.
//
// ## 왜 나누는가
//
// `/api/assistant` 검색은 LLM을 2회 부른다(재작성 + 설명 생성). 그대로 부하를
// 걸면 **우리가 튜닝할 수 없는 OpenAI의 rate limiter를 측정하게 된다** — p95를
// 제3자가 지배하고, 우리 코드를 아무리 고쳐도 숫자가 안 움직인다.
//
//   cache-warm : 소수 질의를 반복 → 시맨틱 캐시 적중, llm_calls=0
//                → **우리 시스템의 천장**(직렬화·라우팅·캐시 조회)
//   live-llm   : 다양한 질의, 낮은 동시성, 짧게
//                → 현실적 지연
//
// 두 모드의 차이가 곧 OpenAI 몫이다. 스텁 클라이언트를 넣는 대신 **이미 있는
// 캐시 경로**를 쓰므로 프로덕션 코드를 건드리지 않는다.
//
// ## 응답 본문에서 단계별 계측을 꺼낸다
//
// ADR-0019에서 `/api/assistant`가 하위 파이프라인의 `timings`를 전파하도록
// 고쳤다. 그래서 k6가 서버 메트릭 없이도 단계별 분해를 Trend로 기록할 수 있다.
//
// 실행:
//   k6 run -e MODE=cache-warm load/k6/ai-search.js
//   k6 run -e MODE=live-llm   load/k6/ai-search.js

import http from "k6/http";
import { check } from "k6";
import { Counter, Trend } from "k6/metrics";

const BASE = __ENV.BASE_URL || "http://localhost:8000";
const MODE = __ENV.MODE || "cache-warm";

// 캐시 적중을 노리므로 소수만 반복한다.
const WARM_QUERIES = ["5만원 이하 검 찾아줘", "100렙 이상 활 찾아줘"];

// 캐시를 피하려고 매번 다른 질의를 만든다. 실제 LLM 호출이 발생한다.
const LIVE_QUERIES = [
  "5만원 이하 검 찾아줘",
  "100렙 이상 활 찾아줘",
  "마법사가 쓸 지팡이 추천",
  "단검 보여줘",
  "얼음속성 지팡이",
  "강화 갑옷",
  "싼 아이템 추천",
  "불속성 검 찾아줘",
];

const llmCalls = new Counter("llm_calls_total");
const cacheHits = new Counter("cache_hits");
const serverError = new Counter("server_5xx");

// 응답 본문의 timings를 그대로 Trend로 옮긴다. 서버 /metrics 와 별개로
// 부하 생성기 쪽에서도 분해가 보이게 한다.
const stage = {
  cache: new Trend("stage_cache_ms", true),
  routing: new Trend("stage_routing_ms", true),
  execution: new Trend("stage_execution_ms", true),
  query_understanding: new Trend("stage_query_understanding_ms", true),
  embedding: new Trend("stage_embedding_ms", true),
  retrieval: new Trend("stage_retrieval_ms", true),
  rerank: new Trend("stage_rerank_ms", true),
  explain: new Trend("stage_explain_ms", true),
};
const KEY_TO_STAGE = {
  cache_ms: "cache",
  routing_ms: "routing",
  execution_ms: "execution",
  query_understanding_ms: "query_understanding",
  embedding_ms: "embedding",
  retrieval_ms: "retrieval",
  rerank_ms: "rerank",
  explain_ms: "explain",
};

export const options = {
  // live-llm은 동시성을 낮게 간다. 올리면 OpenAI rate limit에 걸려서
  // 우리 시스템이 아니라 429 재시도를 측정하게 된다.
  vus: Number(__ENV.VUS || (MODE === "live-llm" ? 3 : 10)),
  duration: __ENV.DURATION || (MODE === "live-llm" ? "30s" : "40s"),
  thresholds: {
    server_5xx: ["count==0"],
    checks: ["rate==1.0"],
  },
};

// 토큰 발급자는 백엔드뿐이므로(ADR-0023) **이 시나리오는 이제 백엔드가 떠 있어야
// 돈다.** k6가 비밀키로 직접 서명하면 백엔드 의존은 사라지지만 발급 로직의 세 번째
// 사본이 생긴다 — 검증기가 두 벌인 것만으로도 충분히 갈라질 위험이 있다.
const BACKEND = __ENV.BACKEND_URL || "http://localhost:8080";

function issueToken(userId) {
  const issued = http.post(
    `${BACKEND}/api/auth/demo-token`,
    JSON.stringify({ userId }),
    { headers: { "Content-Type": "application/json" } },
  );
  if (issued.status !== 200) {
    throw new Error(
      `토큰 발급 실패 (status=${issued.status}). 백엔드(8080)가 떠 있는지 확인하세요.`,
    );
  }
  return issued.json("token");
}

function ask(query, useCache, token) {
  // tenant_code는 본문에서 빠졌다 — 토큰 클레임이 출처다.
  return http.post(
    `${BASE}/api/assistant`,
    JSON.stringify({ query, use_cache: useCache }),
    {
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
      },
      timeout: "120s",
      tags: { mode: MODE },
    },
  );
}

export function setup() {
  // 토큰을 먼저 받는다. 발급 왕복이 측정 구간에 섞이면 안 되므로 여기서 한 번만
  // 한다(TTL 1시간 > 부하 구간 수 분).
  const token = issueToken(3);

  // **워밍업.** 임베딩·리랭커·KoELECTRA가 전부 지연 로딩이라 첫 요청이 수십 초
  // 걸린다(ADR-0019 실측 35.3초). 이걸 본 측정에 섞으면 p95가 통째로 오염된다.
  // 값을 버리지 않고 콜드 스타트 비용으로 보고한다.
  const started = Date.now();
  const first = ask(WARM_QUERIES[0], false, token);
  const coldMs = Date.now() - started;

  let firstTimings = {};
  try {
    firstTimings = first.json().timings || {};
  } catch (e) {
    firstTimings = {};
  }
  console.log(`[warmup] status=${first.status} cold_start_ms=${coldMs}`);
  console.log(`[warmup] timings=${JSON.stringify(firstTimings)}`);

  if (MODE === "cache-warm") {
    // 캐시를 채워둔다. 이후 본 측정은 전부 적중이어야 한다.
    WARM_QUERIES.forEach((q) => ask(q, true, token));
  }
  return { coldMs, firstTimings, token };
}

export default function (data) {
  const useCache = MODE === "cache-warm";
  const pool = useCache ? WARM_QUERIES : LIVE_QUERIES;
  const query = pool[(__VU + __ITER) % pool.length];

  const res = ask(query, useCache, data.token);

  if (res.status >= 500) {
    serverError.add(1);
  }
  check(res, { "200": (r) => r.status === 200 });

  if (res.status !== 200) return;

  let body;
  try {
    body = res.json();
  } catch (e) {
    return;
  }

  llmCalls.add(body.llm_calls || 0);
  if (body.cache && body.cache.hit) cacheHits.add(1);

  const timings = body.timings || {};
  for (const [key, value] of Object.entries(timings)) {
    const name = KEY_TO_STAGE[key];
    if (name) stage[name].add(value);
  }
}

export function teardown(data) {
  console.log(`[teardown] mode=${MODE} cold_start_ms=${data.coldMs}`);
}
