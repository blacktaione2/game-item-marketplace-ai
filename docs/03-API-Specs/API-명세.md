---
status: 현행
updated: 2026-07-31
---

# API 명세

서버가 둘이고 **식별자 규약이 다르다.** 그 차이가 이 문서에서 제일 먼저
알아야 할 사실이다.

| | 백엔드 (Spring Boot) | AI 서버 (FastAPI) |
|---|---|---|
| 포트 | 8080 | 8000 |
| 인증 | `Authorization: Bearer <JWT>` | 동일 (같은 토큰) |
| 테넌트 식별 | 클레임 `tenant_id` (**Long**) | 클레임 `tenant_code` (**str**) |
| 행위자 식별 | 클레임 `sub` | 클레임 `sub` |
| 토큰 발급 | **여기서만** (`POST /api/auth/login`) | 검증 전용 |

**요청에 테넌트·행위자를 싣지 않는다.** 두 서버가 표기(Long vs str)는 다르게
쓰지만 토큰이 둘 다 싣고 다니므로 **출처는 하나**다(ADR-0023). 예전에는
프론트의 `src/demo.ts`가 이 차이를 흡수했는데, 그 값들은 이제 서버로 나가지
않는다.

| 클레임 | 값 | 쓰임 |
|---|---|---|
| `sub` | userId | 행위자 |
| `tenant_id` | `1` | 백엔드 조회 격리 |
| `tenant_code` | `"nexon"` | AI 서버의 ES 인덱스 선택 |
| `role` | `USER` / `ADMIN` | GM 검토 큐 접근 |
| `exp` / `iss` | 1시간 / `gimp-backend` | |

> **로그인은 실제로 동작한다**(ADR-0031). `POST /api/auth/login {username, password}` —
> BCrypt 검증, 자격증명이 틀리면 **401**. **없는 사용자도 401**이다(404로 갈리면
> 사용자 열거가 된다). `demo-token` 은 **제거됐다.**
>
> **회원가입 경로는 없다.** 계정은 시드로 고정이고, 이건 누락이 아니라 결정이다.
>
> 비밀번호는 저장소에 없다 — `DEMO_PASSWORD` / `ADMIN_PASSWORD` 를 기동 시 주입한다.
> **GM 계정만 별도 비밀번호**를 쓴다(하나면 데모 비밀번호를 아는 사람이 곧바로 GM 이
> 되어 이상거래 큐가 열린다).

**공통 상태 코드**: **401**(토큰 없음·무효·만료), **403**(권한 부족),
**429**(한도 초과, `Retry-After` 헤더 동반).
`/actuator/prometheus`, `/actuator/health`, `/api/health`,
`/api/auth/demo-token`, AI의 `/health`·`/metrics`는 인증에서 제외된다 —
부하 하네스와 헬스체크가 읽는 경로다.

## 요청 한도 (ADR-0024)

**비용이 나가거나 토큰 없이 닿을 수 있는 경로에만** 건다. 조회 계열은 걸지 않는다.

| 경로 | 한도 | 키 |
|---|---|---|
| `POST /api/assistant` | 20회 / 분 | tenant + user |
| `POST /api/items/{id}/purchase`, `/bids` | 10회 / 초 | user |
| `POST /api/auth/login` | 30회 / 분 | **IP** |
| `POST /api/assistant` (일일) | **50회 / 일** | tenant + user |

> `login`만 IP를 쓴다 — 인증 이전이라 신원이 없기 때문이다. **NAT·회사망 뒤에서는
> 여러 사용자가 한 IP를 공유하므로 정상 사용자가 막힐 수 있다.**
>
> **`X-Forwarded-For` 는 신뢰하는 프록시에서 온 요청일 때만 읽는다**(ADR-0031).
> 목록(`rate-limit.trusted-proxies`)의 **기본값은 비어 있어** 설정을 안 하면 예전처럼
> 헤더를 무시한다 — 잊은 배포가 더 안전한 쪽으로 실패한다.
>
> **일일 한도는 한국시간 자정에 리셋된다** — 키에 날짜가 들어간다. 고정 윈도우라
> 자정 전후로 몰리면 짧은 시간에 2배가 가능하고, 그건 수용한다(실제 비용 상한은
> OpenAI 월 한도다). **24시간이 아니다** — 언제 처음 물어봤든 자정에 풀린다.
>
> `Retry-After` 는 남은 TTL 이고, 일일 키의 TTL 은 **자정까지 남은 초**다
> (2026-08-07 수정 — 그전엔 86,400이라 최대 하루 가까이 과장했다. ADR-0031 참고).

**부하테스트는 이 한도에 걸린다.** 완화 구성으로 띄우는 절차는 `load/README.md`.

### 운영 중 한도를 지워야 할 때

배포 검증(`load/verify-container.sh`)이 한 실행에 `/api/assistant` 를 **9회**
쓴다. 하루 다섯 번쯤 돌리면 50회가 소진되고, 그때 검사는 **"판정 불가 — 한도
소진(429)"** 이라고 정확히 말한다(그전엔 "게이트가 막았다" / "Redis 를 의심하라"
로 엉뚱하게 보고했다).

```bash
REDIS_PW=$(grep -E '^REDIS_PASSWORD=' .env | cut -d= -f2-)
R() { docker exec gimp-redis redis-cli --no-auth-warning -a "$REDIS_PW" "$@"; }
R --scan --pattern 'ratelimit:assistant*' | while read -r k; do printf '%s = ' "$k"; R GET "$k"; done
R --scan --pattern 'ratelimit:assistant*' | xargs -r docker exec gimp-redis \
  redis-cli --no-auth-warning -a "$REDIS_PW" DEL
```

> 패턴이 `ratelimit:assistant*` 로 좁아 **시맨틱 캐시·구매 한도·Redisson 락은
> 안 건드린다.** 다만 이건 비용 방어를 잠깐 푸는 것이라 검증할 때만 쓴다.
>
> **`REDIS_PASSWORD` 는 호스트에서 전개해야 한다.** compose 의 redis 서비스에는
> `environment:` 블록이 없고 `command:` 의 `${REDIS_PASSWORD}` 는 호스트에서
> 치환되므로, **컨테이너 안에 그 변수는 없다**(`sh -c '... "$REDIS_PASSWORD"'`
> 로 쓰면 빈 값으로 인증해 `WRONGPASS` 가 난다).
>
> 카운터 값이 한도를 넘어 보이는 것은 정상이다 — `INCR` 이 먼저라 **거절된
> 요청도 센다.** 그 값은 허용 횟수가 아니라 **시도 횟수**다.

## 알림 (ADR-0030)

체결 후 **비동기로** 만들어진다. 구매 응답을 받은 직후 조회하면 아직 0건일 수 있고
**그게 정상이다** — 큐가 비면 채워진다.

| 메서드 | 경로 | 설명 |
|---|---|---|
| `GET` | `/api/notifications` | 최근 20건. **수신자는 토큰에서 온다** — 쿼리로 받으면 남의 알림을 읽을 수 있다 |
| `GET` | `/api/notifications/unread-count` | `{ "count": 3 }` |
| `PATCH` | `/api/notifications/read` | 모두 읽음. `{ "count": 0 }` (ADR-0037) |

한 거래가 **둘**에게 알림을 만든다(구매: 구매자·판매자 / 입찰: 입찰자 + 밀려난 이전
입찰자). 멱등 키가 `(recipient_id, trade_id)` 인 이유다.

**개별 읽음 처리는 없다.** 화면에 그 동작이 없어서다. `id` 목록을 받는 형태를 피한
이유는 따로 있는데, 남의 알림 id 를 넣을 수 있어 **소유권 검사를 별도로 붙여야 하기
때문**이다 — 수신자를 토큰에서 받아 조건에 넣으면 그 검사 자체가 필요 없어진다.

## 거래 내역 (ADR-0037)

| 메서드 | 경로 | 설명 |
|---|---|---|
| `GET` | `/api/trades` | 내가 관여한 거래 최근 50건. **구매·판매 양쪽** |

```jsonc
[
  {
    "id": 1, "itemId": 5, "itemName": "미스릴 단검",
    "tradeType": "PURCHASE", "status": "COMPLETED",
    "price": 22000, "quantity": 1,
    "side": "BUY",                        // 조회자가 어느 쪽이었나 (BUY|SELL)
    "counterpartyUsername": "trader_park",
    "createdAt": "2026-08-07T01:12:00"
  }
]
```

**체결 응답(`TradeResponse`)과 필드가 다르다.** 이쪽은 보는 사람이 정해져 있어서
`side` 와 상대 이름이 들어가고, id 대신 이름을 쓴다 — `buyerId: 3` 은 화면에서 아무
뜻이 없다.

조회자는 토큰에서만 온다. `?userId=` 를 받으면 남의 거래 내역을 읽을 수 있다.
페이지네이션 없이 50건 상한만 둔다 — 부하테스트로 수만 건이 쌓인 계정에서 응답을
통째로 밀어 넣지 않기 위해서다.

**구매·입찰 응답은 바뀌지 않았다.** 여전히 `201 Created` 에 거래 정보가 실린다 —
큐로 넘긴 것은 체결이 아니라 그 뒤이기 때문이다(ADR-0030).

## 오리진과 CORS

브라우저는 **단일 오리진만 본다.** 경로 규칙은 개발과 배포가 같고, 그것을
제공하는 주체만 다르다.

```
개발  (Vite dev proxy, :5173)      배포  (nginx, :80 — ADR-0029)
  /api/backend/* -> :8080/api/*      /api/backend/* -> backend:8080/api/*
  /api/ai/*      -> :8000/api/*      /api/ai/*      -> ai:8000/api/*
```

그래서 **양쪽 서버 모두 CORS 설정이 없다. 추가하지 말 것.**
`load/verify-container.sh` 가 이걸 **행동으로** 단언한다 — `Origin` 헤더를
붙여 보내고 `access-control-*` 가 안 나오는지 본다.

> 두 곳의 경로 규칙이 **어긋나면 개발에서는 되고 배포에서만 깨진다.**
> `vite.config.ts` 를 고치면 `frontend/nginx.conf` 도 같이 고칠 것.

**AI 서버의 헬스 경로는 `/health` 지 `/api/health` 가 아니다.** 즉 프록시
경유로는 `/api/ai/health` 가 **404**다(라우터에 `/api` 접두사가 없다).
이걸 모르고 검사를 짜서 한 번 헛돌았다 — ADR-0029.

---

# AI 서버 (FastAPI, :8000)

## `POST /api/assistant` — 통합 진입점

의도를 판별해 알맞은 파이프라인으로 보낸다. **개별 엔드포인트를 직접 부르는
것보다 이쪽이 기본 경로다.** 설계는
`docs/02-AI-Pipeline/요청-타입별-파이프라인.md`.

**요청**

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `query` | str | O | 자연어 질의. **1~500자** — 넘으면 **422** |
| `use_cache` | bool | | 기본 `true`. 캐시 효과 측정·디버깅용으로 끌 수 있다 |

> **길이 상한이 비용 방어의 일부다**(ADR-0035). 한도 3계층은 전부 **요청 수**를
> 세므로, 길이가 자유면 한도를 지키면서 하루 예산을 배수로 늘릴 수 있다. 실측:
> 19,800자 질의가 200으로 통과하고 16.4초 걸렸다(정상 ~50자 / 4.5초).
>
> 500자 근거 — 임베딩은 **128토큰**, 리랭커는 **256토큰**에서 자르므로 그 너머는
> LLM 요금만 늘린다. 그리고 데이터셋 질의 547건의 **최댓값이 33자**다(15배 여유).

테넌트는 토큰 클레임에서 온다. 시맨틱 캐시 키가 테넌트별로 갈리므로,
본문으로 받던 동안에는 **남의 테넌트 캐시를 조회하도록 요청할 수 있었다**
(ADR-0023).

**응답 — 공통 필드**

```jsonc
{
  "query": "5만원 이하 검 찾아줘",
  "intent": "item_search",          // faq_smalltalk|item_search|price_forecast|anomaly_check|compound|unknown
  "routing": {
    "decided_by": "rules",          // rules|classifier|low_confidence|fallback_no_model
    "confidence": null,             // 분류기가 판정했을 때만 숫자
    "initial_intent": "item_search" // intent와 다르면 분기가 에스컬레이션한 것
  },
  "answer": "…",
  "llm_calls": 2,                   // 의도마다 다르다 — 아래 표
  "cache": { "hit": false },
  "timings": { "routing_ms": 0.1, "execution_ms": 2140.5 }
}
```

> `decided_by`는 넷이다. `low_confidence`는 분류기가 임계값 아래라 COMPOUND로
> 보낸 것이고(그때만 `classifier_intent`가 같이 온다), `fallback_no_model`은
> 분류기가 아직 학습되지 않아 룰만으로 돈 것이다 — **분류기가 없다고 라우팅이
> 죽지는 않는다.**

**미적중일 때 의도별 `llm_calls`**

| 의도 | 호출 | 내역 |
|---|---|---|
| `faq_smalltalk` | **0** | 확정 응답. LLM 을 안 부른다 |
| `item_search` | **2** | 질의 이해 + 도메인 판정(**병렬**). 설명 생성은 없다 (ADR-0036·0039) |
| `price_forecast` | **3** | 위 둘 + 설명 |
| `anomaly_check` | 1 | 설명. 검색을 타지 않아 도메인 판정이 없다 |
| `compound` | 도구 수 + 1 | 에이전트 |

캐시 적중 시 `cache`는 `{hit, match_type, similarity, cached_query}`가 되고
**`llm_calls`는 항상 0**이다.

**의도별 추가 필드**

| `intent` | 추가 필드 | `llm_calls` |
|---|---|---|
| `faq_smalltalk` | — | **0** |
| `item_search` | `results[]` | **2** |
| `item_search` (0건) | `no_results: true`, `conditions[]`, `applied_filters` | **2** |
| `item_search` · `price_forecast` (도메인 밖) | `out_of_domain: true`, `results: []` | **2** |
| `price_forecast` | `forecast`, `resolved_item` | **3** |
| `anomaly_check` | `detection` | 1 |
| `compound` | `tool_calls[]`, `tool_failures`, `stop_reason` | 1~6 (도구 호출 수 + 1) |

> **검색을 타는 분기는 질의이해 + 도메인 판정으로 2회다** (ADR-0039). 둘은
> `asyncio.gather`로 **동시에** 나가므로 지연은 하나치인데 **비용은 둘**이다.
> `timings`에 `query_understanding_ms`와 `domain_gate_ms`가 따로 찍히는데,
> 병렬이라 **두 값을 더하면 실제 지연보다 크게 나온다** — 검색 전체 시간은
> `execution_ms`로 봐야 한다.
>
> 이 값은 세 번 바뀌었다: 2(검색 설명 LLM 있던 시절) → 1(ADR-0036) →
> 2(ADR-0039). 바뀌지 않은 건 **검색 분기 전체가 같은 값**이라는 점이다.
> 그래서 `llm_calls`로는 0건 여부도, 도메인 밖 여부도 알 수 없다 — 각각
> `no_results`와 `out_of_domain`을 읽어야 한다.

**0건 응답 예시** — 조건을 같이 돌려주는 것이 요점이다. 결과가 있으면 항목이
스스로 종류·속성을 밝히지만 0건에는 검증할 대상이 없다.

```jsonc
{
  "intent": "item_search",
  "answer": "검 · 화염 속성 · 30,000원 이하 조건에 맞는 매물이 없습니다. 조건을 완화하면 결과가 나올 수 있습니다.",
  "results": [],
  "no_results": true,
  "conditions": ["검", "화염 속성", "30,000원 이하"],
  "applied_filters": { "category": "무기", "subcategory": "검", "element": "화염", "price_max": 30000.0 },
  "llm_calls": 1
}
```

> `llm_calls`가 0이 아니라 **1**이다. 질의 이해 호출은 건너뛸 수 없다 —
> 어떤 필터가 걸렸는지 알아야 0건 판정이 선다.
>
> **역은 성립하지 않는다.** 예전 판본은 여기에 "`llm_calls == 0`은 캐시 적중을
> 뜻한다"고 적었는데 틀렸다 — `faq_smalltalk`은 확정 응답이라 **미적중에서도
> 0**이다. 실배포에서 `hit=false, llm_calls=0`으로 실측됐다. 적중 여부는
> `cache.hit`으로 읽고, `llm_calls`는 분기 비용만 말한다.

**도메인 밖 응답 예시** (ADR-0039) — 이 거래소가 다루지 않는 주제다.

```jsonc
{
  "intent": "price_forecast",
  "answer": "게임 아이템·계정·게임 재화 거래에 관한 질문에만 답변할 수 있습니다. 아이템 검색, 시세 확인, 이상거래 점검을 도와드립니다.",
  "results": [],
  "out_of_domain": true,
  "llm_calls": 2
}
```

> **질의를 되풀이하지 않는다.** "삼성전자 주식은 다루지 않습니다"처럼 대상을
> 받아 적으면, 게이트가 틀렸을 때(도메인 안을 거절했을 때) 그 문장이 오히려
> 설득력을 갖는다. `_out_of_domain()`은 아예 질의를 인자로 받지 않는다.
>
> `llm_calls`가 **0이 아니라 2**인 이유: 판정과 질의이해가 병렬로 함께
> 나갔으므로, 도메인 밖이라는 걸 알았을 때는 이미 둘 다 돈 뒤다. 판정을 먼저
> 하고 통과할 때만 이해시키면 여기서 1을 아낄 수 있지만, 그 대가는 **모든
> 요청의 지연**이라 택하지 않았다.
>
> `no_results`와 **다른 판정**이다. 0건은 조건을 완화하면 결과가 나올 수
> 있고, 이건 완화할 조건이 없다. 둘 다 캐시에 저장되지 않지만 **이유가 다르다**
> — 0건은 "가장 낡기 쉬운 답"이라서고, 도메인 밖은 낡지 않는다(내일도 밖이다).
> 저장하지 않는 근거는 오직 **판정이 비결정적**이라는 것 하나다.
>
> `/api/search`도 같은 게이트를 지난다 — `in_domain: false`, `results: []`를
> 내고 임베딩·ES·리랭킹을 아예 건너뛴다.

**상태 코드**

| 코드 | 조건 |
|---|---|
| 404 | 테넌트 인덱스 없음 |
| 503 | 예측/이상탐지 모델 미학습. 본문에 실행할 명령이 들어 있다 — `python -m scripts.train_forecast` / `train_anomaly` |
| 500 | 그 외 |

`stop_reason`은 `max_steps`(설정 `agent_max_steps`, 기본 5)에 걸렸는지를 알려준다.

---

## `POST /api/search`

MCP 도구가 감싸고 있어 남겨둔 개별 엔드포인트. 라우팅·캐시를 건너뛴다.

**요청**: `query`(필수, **1~500자**), `size`(1~50, 기본 10),
`use_rerank`(기본 `true` — 리랭킹 전/후 비교용)

> ~~`tenant_code`~~ **는 본문에 없다**(ADR-0022) — 토큰 클레임에서 온다. 같은 사실의
> 출처가 둘이면 어긋날 수 있고 어긋난 걸 검출할 방법이 없다. 이 줄은 그 변경 뒤에도
> "필수"로 남아 있던 드리프트였고 ADR-0035 점검에서 잡았다.
>
> `size`는 원래 묶여 있었는데 `query`는 아니었다 — **같은 DTO 안의 그 비대칭이
> 상한 누락의 발견 단서였다**(ADR-0035).

**응답**

```jsonc
{
  "query": "5만원 이하 검",
  "rewritten_query": "검 소드 대검 단검",
  "filters": { "category": "무기", "subcategory": "검", "price_max": 50000.0 },
  "in_domain": true,
  "reranked": true,
  "timings": { "query_understanding_ms": 0, "domain_gate_ms": 0, "embedding_ms": 0, "retrieval_ms": 0, "rerank_ms": 0 },
  "results": [
    {
      "item_id": 5, "tenant_id": 1,
      "name": "미스릴 단검", "description": "…",
      "category": "무기", "subcategory": "검", "element": "무속성",
      "sale_type": "FIXED_PRICE", "status": "ON_SALE",
      "price": 22000, "enhancement_level": 0, "required_level": 45,
      "rrf_score": 0.032, "bm25_rank": 1, "knn_rank": 3, "rerank_score": -2.71
    }
  ]
}
```

`filters`는 `exclude_none`이라 **걸리지 않은 조건은 키 자체가 없다.**

`in_domain`이 `false`면 `results`는 빈 배열이고 **`embedding_ms` 이후의 계측 키가
아예 없다** — 임베딩·ES·리랭킹을 건너뛰기 때문이다(ADR-0039). 이 엔드포인트는
판정만 실어 보내고 거절 문구는 만들지 않는다. 무엇을 할지는 호출자가 정한다.

`query_understanding_ms`와 `domain_gate_ms`는 **병렬로 나간 두 호출이라 더하면
안 된다.**

`rerank_score`는 크로스인코더 로짓이라 **전부 음수일 수 있고, 질의를 가로질러
비교할 수 없다.** 이 값에 전역 임계값을 걸려는 시도는 두 번 측정해서 두 번
기각됐다 (ADR-0018).

---

## `POST /api/forecast`

**요청**: `item_id`(필수), `horizon`(1~30, 생략 시 모델 기본). 테넌트는 클레임에서.

**응답**

```jsonc
{
  "item_id": 1, "name": "+9 강화 롱소드", "category": "무기",
  "cold_start": false,
  "history_days": 120,
  "history":  [ { "date": "2026-07-01", "price": 45200.0 } ],
  "anchor_price": 45000.0,
  "horizon_days": 7,
  "forecast": [ { "date": "2026-08-01", "price": 45900.0, "ratio": 1.02 } ],
  "expected_change_pct": 2.0,
  "inherited_from": null,
  "timings": { "window_ms": 0, "inference_ms": 0 }
}
```

`cold_start: true`면 **거래 이력이 부족해 유사 아이템 추세를 상속한 추정치**이며
`inherited_from`에 출처가 담긴다. 응답을 사용자에게 보여줄 때 이 사실을 반드시
밝혀야 한다.

`history`는 최근 30일이며 그래프의 실선용이다(`forecast`가 점선).

**상태 코드**

| 코드 | 조건 |
|---|---|
| 404 | 테넌트 인덱스 없음 / 아이템 없음 |
| 422 | 이력 부족(Cold Start로도 처리 불가) |
| **503** | **모델 미학습** — 본문에 `python -m scripts.train_forecast` |
| 400 | `horizon`이 모델 학습값을 초과 |

---

## `POST /api/anomaly/detect`

**요청**

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `trade_ref` | str | O | `"syn:3"` / `"pg:3"` — **접두사가 공간을 지정한다** |

> **참조가 자기 공간을 들고 온다** (ADR-0022). 합성 코퍼스(거래 1~26,702)와
> PostgreSQL(거래 1~N)의 id 범위가 **겹친다.** `3`은 양쪽에서 유효하고 서로
> 다른 거래를 가리키므로 범위 검사로 구분할 수 없다. 그래서 접두사 없는
> `"3"`은 추측하지 않고 **400**으로 거부한다.
>
> 2026-08-01 이전에는 `trade_id: int` + `id_space` 두 필드였다. 한 값으로
> 합친 이유는 두 필드가 **서로 어긋나게 올 수 있고 어긋난 것을 검출할 방법이
> 없기** 때문이다.
>
> **400과 501은 다른 답이다.** `"pg:3"`은 **잘 만들어진 요청**이므로 400이
> 아니라 **501**을 받는다 — 요청이 틀린 게 아니라 서버가 아직 그 평면을 못
> 읽는다. `SUPPORTED_SPACES`에 `BACKEND`를 추가하면 열리지만, **파싱이 된다고
> 처리가 되는 것은 아니다** — 백엔드 거래를 11개 특징 축으로 변환하는 코드가
> 따로 필요하다(ADR-0022).

**요청 예시**

```jsonc
{ "tenant_code": "nexon", "trade_ref": "syn:3" }
```

**응답**

```jsonc
{
  "trade_id": 23659, "item_id": 4, "buyer_id": 91, "seller_id": 12,
  "id_space": "synthetic",
  "price": 402000.0, "quantity": 1,
  "market_median": 300000.0, "price_ratio": 1.34,
  "traded_at": "2026-07-20T03:14:00",
  "anomaly_score": 0.0412, "threshold": 0.0188, "is_anomaly": true,
  "contributions": [ { "feature": "log_price_ratio", "share": 0.41 } ],
  "injected_label": "price_spike",
  "alert_percentile": 99.0,
  "timings": { "scoring_ms": 1.2 }
}
```

`contributions`가 이 모델을 고른 이유다 — 재구성 오차를 피처별로 쪼개 "왜
이상인가"를 축 단위로 답한다.

`injected_label`은 **합성 데모 데이터의 정답 라벨**이라 판정이 맞았는지 눈으로
확인하는 용도다. 실데이터에는 없다.

응답은 `trade_id`(정수)와 `id_space`를 **따로** 준다. 요청과 형태가 다른데
의도적이다 — 화면이 이 둘을 조합해 `합성#3`으로 렌더링하고 있고
(`AnomalyQueue.tsx`), 여기에 `trade_ref`를 더하면 같은 사실의 출처가 둘이 된다.

**상태 코드**: **400**(접두사 없음·해석 불가), 404(거래/테넌트 없음),
**501**(미연동 id 공간), 503(모델 미학습), 500

---

## `GET /api/anomaly/alerts`

**쿼리**: `limit`(1~100, 기본 10). 테넌트는 클레임에서.

> **GM 전용 — `role=ADMIN`이 아니면 403.** 이 큐는 다른 사용자의 거래 내역과
> 상대방 id를 그대로 보여준다. 이 프로젝트에서 역할 인가가 실제로 의미를 갖는
> 유일한 지점이다(ADR-0023). 프론트가 메뉴를 숨기는 것은 접근 제어가 아니었다 —
> URL을 직접 치면 열렸다.

**응답**: `{tenant_code, threshold, alert_percentile, total_trades, total_alerts, alerts[]}`
— `alerts[]`의 각 항목은 `/detect` 응답과 같은 형태다.

> **이 경로는 id 공간 가드를 지나지 않는다** (ADR-0022). 외부에서 id를 받지
> 않고 합성 코퍼스만 훑기 때문에 검사할 대상 자체가 없고, 각 항목의
> `id_space`는 `"synthetic"`으로 **하드코딩**돼 있다.
>
> 이 목록에 백엔드 거래가 섞이는 순간 그 하드코딩이 거짓이 되고, 화면은 합성
> 유저 번호를 실제 번호처럼 보여주게 된다. **실데이터를 섞으려면 거래마다
> 공간을 들고 다니도록 먼저 바꿔야 한다.**

---

## ~~`POST /api/llm/test`~~ — 제거됨 (ADR-0033)

Phase 2의 OpenAI 왕복 확인용이었다. **인증 의존성이 없었다** — 다른 다섯 라우터가
전부 `Depends(require_actor)`를 달 때 여기만 빠졌고, nginx가 `/api/ai/*`를 그대로
넘기므로 **토큰 없이 임의 프롬프트를 OpenAI로 보낼 수 있었다.** 비용 방어
3계층(토큰 요구 · 20회/분 · 50회/일)을 통째로 우회한다.

되살리지 않는다 — 연동 확인은 `/health`와 `/api/assistant`가 대신한다.
`tests/test_route_auth_coverage.py`가 **인증 없는 경로가 존재하지 않음**을 고정한다.

## `GET /health`

`{"status": "ok", "service": "ai-server"}`

## `GET /metrics`

Prometheus 텍스트 포맷. **Prometheus를 띄우지 않아도 쓸모가 있다** — 히스토그램은
누적값이라 부하테스트 전후로 받아 차분하면 실행 구간의 정확한 집계가 나온다.

| 메트릭 | 종류 | 라벨 |
|---|---|---|
| `ai_stage_duration_seconds` | Histogram | `stage`, `tenant` |
| `ai_requests_total` | Counter | `tenant`, `intent`, `outcome` |
| `ai_llm_calls_total` | Counter | `tenant`, `intent` |
| `ai_cache_lookups_total` | Counter | `tenant`, `result` |
| `ai_rate_limited_total` | Counter | `tenant`, `path` |

> `ai_rate_limited_total`은 **`record_response()`를 지나지 않는다** — 한도 거절은
> 파이프라인 진입 전에 끝나 응답 자체가 만들어지지 않기 때문이다. "계측 지점은
> 하나"라는 원칙(ADR-0019)의 유일한 예외다. 백엔드 쪽 짝은
> `rate_limited_total{scope}`이며, `trade.rejection`에 합치지 않았다 — 토큰
> 발급은 거래가 아니라 이름이 거짓이 된다.

`stage`: `cache`(= `cache_encode` + `cache_lookup`) · `routing` · `execution` ·
`query_understanding` · **`domain_gate`** · `embedding` · `retrieval` · `rerank` ·
`explain` · `forecast_window` · `forecast_inference` · `anomaly_scoring` ·
`agent_llm` · `agent_tool`

> `query_understanding`과 `domain_gate`는 **병렬로 나가는 두 LLM 호출**이라
> (ADR-0039) 더하면 실제 지연보다 크다. 검색 전체는 `execution`으로 본다.
> 적중 경로에서 `cache_encode`는 **없어야** 정상이다(ADR-0026) — 있으면 회귀다.

> `cache`를 둘로 가른 이유는 적중 경로의 비용을 귀속시키려면 재야 했기 때문이다
> (ADR-0025).
>
> **[정정] 그때 나온 "`cache_lookup` 73% / `cache_encode` 27%"를 비율 그대로
> 읽으면 안 된다.** 그건 10 VU 부하 중의 **벽시계 시간**이고, 격리해서 재면
> `lookup()`이 **1.05ms**, `encode_one`이 **15.77ms**다. `encode_one`이 `async`
> 핸들러 안의 동기 CPU 호출이라 이벤트 루프를 막았고, **73%는 나머지 요청들이
> 그 27%를 기다린 시간**이었다. 지연화 후 p95가 279ms → 25.9ms 가 됐다
> (ADR-0026). 즉 ADR-0020의 "임베딩 낭비"라는 지목은 **결과적으로 옳았고**
> ADR-0025의 반박이 틀렸다 — **부하 중 벽시계 시간은 그 단계의 일이 아니다.**

`outcome`: `ok` · `out_of_domain` · `no_results` · `tool_failure` — 뒤 셋은
에러가 아니지만 부하테스트에서 구분해서 봐야 한다.

> `out_of_domain`이 **운영에서 게이트를 보는 유일한 창**이다 (ADR-0039).
> 오거부율은 배포 전에 평가셋으로 한 번 쟀을 뿐이고 실제 질의 분포는 그것과
> 다르다 — 이 값이 갑자기 늘면 게이트가 멀쩡한 질의를 막고 있다는 뜻이다.

> **라벨에 `item_id`·`trade_id`·`user_id`·질의 문자열을 넣지 말 것.** 전부
> 무한히 늘어나는 값이라 시계열이 폭발한다. "어떤 아이템이 느렸나"는 메트릭이
> 아니라 로그로 답할 문제다.

---

# 백엔드 (Spring Boot, :8080)

## `POST /api/auth/login`

**인증 없이** 호출한다 — 토큰을 받으러 오는 길이다. **IP 단위 30회/분.**

**요청**: `{ "tenantCode": "nexon", "username": "buyer_lee", "password": "..." }` →
**응답**: `{ "token", "expiresIn": 3600, "userId", "username", "role" }`

**역할은 요청이 아니라 DB에서** 읽어 클레임에 싣는다.

> **`tenantCode`가 자격증명의 일부다**(ADR-0034). 아이디는 테넌트 안에서만 유일하므로
> (제약이 `(tenant_id, username)`) 아이디 하나로는 계정이 특정되지 않는다. 이건
> **로그인 한 곳뿐**이며, 발급 이후의 요청은 여전히 테넌트를 싣지 않고 토큰에서 읽는다.

| 상황 | 응답 |
|---|---|
| 비밀번호 불일치 | **401** |
| 없는 사용자 | **401** — 404와 갈리면 **사용자 열거**가 된다. 메시지도 같다 |
| 없는 테넌트 | **401** — 갈리면 테넌트 목록이 새어 나간다 |
| `tenantCode` 누락 | **400** — 자격증명이 틀린 게 아니라 요청이 불완전하다 |
| 한도 초과 | **429** + `Retry-After` |

> **400과 401이 실제로 갈린다.** 예전에는 검증 실패조차 401로 나갔다 — `/error`가
> `PUBLIC_PATHS`에 없어 Boot의 에러 디스패치가 보안 체인에 다시 걸렸기 때문이다.
> 그 탓에 **서버 오류가 "비밀번호가 틀렸다"로 보였다**(ADR-0034).

> **회원가입 경로는 일부러 없다.** 계정은 시드 고정이다 — 등록을 열면 이메일
> 인증·비밀번호 재설정·스팸 계정 방어가 딸려온다(ADR-0031).

> **비밀번호는 저장소에 없다.** `seed-demo.sql`의 해시는 29자 자리표시자라 어떤
> 비밀번호와도 매칭되지 않고, 실제 값은 `DemoAccountInitializer`가 기동 시
> `DEMO_PASSWORD` / `ADMIN_PASSWORD`에서 주입한다. 안 넣으면 **아무도 로그인하지
> 못하는 쪽**으로 실패한다.

> ~~`POST /api/auth/demo-token`~~ **은 제거됐다**(ADR-0031). 비밀번호를 확인하지
> 않아 userId만 알면 누구나 그 사용자의 토큰을 받을 수 있었다. `AuthenticationTest`가
> **유효한 토큰을 들고** 404를 확인한다 — 인증 없이 가면 보안 계층이 먼저 401을
> 내는데 그건 "핸들러가 없다"의 증거가 아니기 때문이다.

## 아이템

전부 **인증 필요**. 테넌트·행위자는 클레임에서 오므로 요청에 싣지 않는다 —
`X-Tenant-Id` / `X-User-Id` 헤더는 **제거됐고, 보내도 무시된다.**

| 메서드 | 경로 | 행위자 쓰임 | 성공 |
|---|---|---|---|
| POST | `/api/items` | `sub` = 판매자 | **201** |
| GET | `/api/items/{itemId}` | — | 200 |
| GET | `/api/items?page=&size=` | — | 200 (`Page<ItemResponse>`) |
| PUT | `/api/items/{itemId}` | `sub` = 요청자(판매자 검사) | 200 |
| DELETE | `/api/items/{itemId}` | `sub` = 요청자(판매자 검사) | **204** |

조회는 전부 `tenant_id` 클레임으로 격리된다. **다른 테넌트의 아이템은 404**이지
403이 아니다 — 존재 여부를 알려주지 않는다.

**`ItemCreateRequest`**: `name`(필수), `description`, `saleType`
(`FIXED_PRICE`\|`AUCTION`, 필수), `price`(> 0, 필수), `stock`(>= 0, 필수)

**`ItemUpdateRequest`**: `name`(필수), `description`, `price`(> 0, 필수)

**`ItemResponse`**

```jsonc
{
  "id": 1, "tenantId": 1, "sellerId": 1, "sellerUsername": "seller01",
  "name": "+9 강화 롱소드", "description": "…",
  "saleType": "FIXED_PRICE", "price": 45000.00,
  "currentBidPrice": null, "currentBidderId": null,
  "stock": 10, "status": "ON_SALE",
  "createdAt": "2026-07-28T10:00:00", "updatedAt": "2026-07-28T10:00:00"
}
```

> **`enhancement_level`·`required_level`이 없다.** 그 두 필드는 Elasticsearch
> 문서에만 있고 PostgreSQL 스키마에는 없다. 상세 화면은 백엔드를 **거래 상태의
> source of truth**로 쓰고 강화 수치는 검색 결과(ES)에서 넘어온 값이 있을 때만
> 보조로 표시한다.

## 거래

| 메서드 | 경로 | 헤더 | 성공 |
|---|---|---|---|
| POST | `/api/items/{itemId}/purchase` | Tenant, User(구매자) | **201** |
| POST | `/api/items/{itemId}/bids` | Tenant, User(입찰자) | **201** |

**요청**: `{"quantity": 1}` (>= 1) / `{"bidPrice": 320000}` (> 0)

**`TradeResponse`**: `{id, tenantId, itemId, buyerId, sellerId, tradeType, price, quantity, status, createdAt}`

### 동시성

구매·입찰은 **Redis 분산 락**으로 보호된다. 경합 시 **409**가 나가고, 그건
버그가 아니라 이 프로젝트가 보여주려는 동작이다. 프론트는 낙관적 업데이트 없이
성공/실패를 그대로 표시한다.

`redisson-spring-boot-starter`는 Boot 4와 호환되지 않는다(재배치된
`RedisProperties`를 참조해 컨텍스트 기동이 실패). 순수 `org.redisson:redisson`에
`RedissonClient` `@Bean`을 직접 정의해 쓴다.

## `GET /api/health`

FastAPI 헬스체크를 프록시한다.
`{backend: "UP", aiServerStatus: "UP"|"DOWN", aiServer: {status, service}|null}`

## 오류 응답

`GlobalExceptionHandler`가 `{"message": "…"}` 형태로 통일한다.

| 코드 | 발생 |
|---|---|
| 400 | 요청 본문 검증 실패 (`MethodArgumentNotValidException`) |
| 404 | `ResourceNotFoundException` |
| **409** | 거래 요청 불가 / **낙관적 락 충돌** / **분산 락 획득 실패** |

---

## 두 저장소의 id 정합성

**아이템 id는 두 저장소에서 같은 것을 가리킨다** — 시딩으로 맞춰뒀기 때문이다.
그래서 검색 결과(ES)를 클릭해 상세(PostgreSQL)로 이동하는 게 성립한다.

**유저와 거래는 다르다.** 합성 코퍼스는 유저 1~206 / 거래 1~26,702, PostgreSQL은
유저 1~5 / 거래 1~N이라 **범위가 겹치는데 서로 다른 엔티티**다. 그래서 거래
참조는 `"syn:3"`처럼 **접두사로 공간을 들고 온다**(위, ADR-0022).

> **코퍼스 아이템을 바꾸면 `ai/scripts/export_demo_sql.py`를 다시 돌리고 SQL을
> 적용해야 한다.** 안 하면 PostgreSQL과 Elasticsearch가 어긋나 검색 결과가
> 실제 행으로 해석되지 않는다.

## 관련 문서

- 파이프라인 설계: `docs/02-AI-Pipeline/요청-타입별-파이프라인.md`
- 연동 구조 결정: `docs/01-Decisions/0013-프론트-백엔드-연동-구조.md`
- 동시성 제어: `docs/01-Decisions/0001-item-동시성-제어-redis-락-낙관적-락-병행.md`
- id 공간 균열: `docs/05-Troubleshooting/저장소-분리로-인한-id-공간-균열.md`
- Boot 4 호환성: `docs/05-Troubleshooting/spring-boot-4-autoconfigure-공통패턴.md`
