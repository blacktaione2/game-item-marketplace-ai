# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Working principles

### 1. Think Before Coding
- Design before coding, critically review the design, and redesign if necessary.
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them instead of choosing silently.
- If a simpler approach exists, say so.
- If ambiguity materially affects correctness, ask before implementing.

### 2. Simplicity First
- Write the minimum code that solves the problem.
- No features beyond what was requested.
- No unnecessary abstractions for single-use code.
- No speculative flexibility or configurability.
- Avoid unnecessary error handling for impossible scenarios.
- Prefer the simplest solution that satisfies the stated requirements.

### 3. Surgical Changes
- Touch only what is required.
- Match the existing code style, formatting, and conventions.
- Preserve existing variable, function, and class names unless explicitly requested.
- Prefer extending existing code over introducing new architecture.
- Don't refactor or clean up unrelated code.
- Remove only imports, variables, or functions made unused by your own changes.
- If you notice unrelated bugs or potential issues, mention them separately instead of modifying them.

### 4. Goal-Driven Execution
- Define clear success criteria before implementing.
- For multi-step tasks, provide a brief implementation and verification plan.
- When practical, verify changes with existing tests or add focused tests when appropriate.
- Confirm the requested behavior before considering the task complete.
- After implementation, explain why each change was necessary.
- Mention any possible side effects or limitations.
- Do not optimize unless explicitly requested or the current implementation has a measurable issue.
- If there are any newly added or modified codes, please provide them along with your answer.

## Project status

Phases 0–4 of `docs/00-Architecture/개발_로드맵.md` are done (infra, backend
skeleton, AI server skeleton, search pipeline, embedding fine-tuning, Phase 5's
price forecasting + Cold Start backoff and Autoencoder anomaly detection, and
Phase 6's intent router + internal-tool MCP + agent + semantic cache).
Phase 7 (React frontend) is done too, and **both hard filters are done** —
`subcategory` (ADR-0014) and `element` (ADR-0015). Same mechanism each time:
keyword mapping + `term` clause + prompt vocabulary + import-time guard + one
reindex. Measured on the same eval set, unfit results went **50 → 26 → 14 with
zero loss of fit results** at every step.

**A global reranker score floor does not work here, and the reason is not the
one originally recorded** — see `scripts/evaluate_rerank_floor.py`, ADR-0018.
ADR-0014 first rejected it because query-rewrite noise (up to 1.31 points)
swamped the 0.3–0.6 fit/unfit margin. Fixing `temperature` (ADR-0017) genuinely
removed that: spread fell to **0.319 against a 0.60 margin**, ten collections
agreed, rank inversions were gone, and an absolute floor `T = −4.34` looked
adoptable. Then enumerating all 126 tune/holdout splits of the same data killed
it: **the inequality that made the threshold safe holds in only 44% of splits**,
the split actually in use sat at the 83rd percentile, and **no margin is both
safe across splits and useful** (at margin ≥ 4.0 every split is safe and nothing
gets cut).

The root cause is structural: per-query fit minimums range **−3.84 to +2.90
(6.74 points)** while the global unfit maximum is −2.54, so a globally safe cut
sits below everything worth cutting. Cross-encoder logits are only meaningful
*within* one query's candidate set — which is also why the relative gap failed
(its `top1` anchor swings 5.7 points). **Do not revisit this because noise got
better; noise is no longer the blocker.** It needs per-query score normalisation
that is demonstrably comparable across queries, or a signal not tied to query
scale.

Category mixing was real and visible in the UI, but the cause was **not** what
the roadmap originally claimed: BM25 behaves correctly (nori keeps `단궁` whole
so it never matches `검`) and the bow came in at **kNN rank 2** from the Phase 4
fine-tuned embedding.

**"No result satisfies the constraints" handling is done too** (ADR-0016): an
empty search result now produces a deterministic answer with **no explanation
LLM call**, naming the filters it searched with (`검 · 화염 속성 · 30,000원
이하 조건에 맞는 매물이 없습니다`). Five runs of the old path gave five
different wordings; the new one gives one. Two things about it are easy to get
wrong:

- **`llm_calls` is not 0 on this path.** `understand_query` inside `run_search`
  already ran and **cannot be skipped** — the verdict is defined by which filters
  got extracted. The value has changed three times: 2 (explanation LLM era) → 1
  (ADR-0036) → **2 (ADR-0039, the domain gate is a second call in parallel)**.
  What has not changed is that **the whole search branch reports the same number**,
  so `llm_calls` separates neither "found" from "found nothing" nor either from
  "out of domain". Read `no_results` / `out_of_domain`. The trigger was a hallucination that the structured results
  directly contradicted: given four swords at 22,000–45,000원 for `10만원 이하 검`,
  the explanation said *"there are no swords under 100,000원 … all exceed it or
  are not swords"*. The prompt told the model to drop items whose type didn't
  match while `_brief()` passed `category` (`무기`) and **not** `subcategory`
  (`검`) — it was asked to re-adjudicate what ADR-0014/0015's hard filters had
  already decided, with less information than the filter had. That instruction
  was a fossil from before those filters existed. General rule: **when a
  deterministic stage already guarantees a property, do not ask the LLM to
  re-check it — it will invent grounds.** The answer is now built by
  `_search_answer()` from the same `_describe_filters()` the empty path uses.
  **`llm_calls == 0` does NOT identify a cache hit** — that earlier claim here was
  wrong. `FAQ_SMALLTALK` answers deterministically and reports 0 on a *miss* too
  (`pipeline.py`'s `{"answer": _faq_answer(query), "llm_calls": 0}`), measured on
  the live deploy as `hit=False, llm_calls=0`. Read `cache.hit` for that question;
  `llm_calls` only says what the branch cost. A verification check that asserted
  `llm_calls == 0` to prove caching worked was **vacuous for exactly this reason**
  — if you probe the cache, probe with an intent whose uncached cost is nonzero.
- **A no-result response is never stored in the cache** — `is_cacheable()` takes
  the response and vetoes on `no_results`. The verdict rests on non-deterministic
  filter extraction, so caching it by query string would freeze one arbitrary
  draw for the whole TTL, and "nothing exists" is the answer that goes stale
  fastest. Don't confuse this with the roadmap's *rewrite* caching idea (query →
  rewritten query), which is fine and would help.

**Out-of-domain queries are refused too** (ADR-0039), and the trigger was a
confident lie: `"삼성전자 주식 어때?"` answered *"삼성전자 주식의 최근 거래가는
약 26,090원"*. Every number was real — they were `게임 머니 1000만 골드`'s actual
forecast. **Only the subject was false.** Three things about it are load-bearing:

- **The judgment is its own LLM call, run in parallel with `understand_query`,
  and putting it in that call's JSON schema was measured and rejected — twice.**
  Adding an `in_domain` field cost **−0.234 → −0.248 rewrite token-set agreement
  against a same-prompt control**, i.e. the loss did not move when the wording
  was fixed, so it was the schema growing, not the words. Rewrite text feeds both
  BM25 and kNN. Split out, the regression is **structurally zero** and
  `test_domain_gate.py::TestExtractionPromptStaysClean` pins it. Cost is one more
  call (search `llm_calls` 2, forecast 3); **latency cost is ~0 because the gate
  is always the faster of the two** (measured 636–900ms vs 1237–2769ms), so never
  add `query_understanding_ms` and `domain_gate_ms` together.
- **The FAQ branch was left alone on purpose.** 9 of 38 out-of-domain queries
  route there and `_DEFAULT_FAQ` already declines by naming the scope, with 0 LLM
  calls. A gate on a path that is already correct is decoration, and decoration
  sells false confidence. Coverage is search+forecast (22) + agent (7) + FAQ (9)
  = 38/38.
- **Prompt wording took five iterations and two of them made it worse.** False
  rejection changed identity each round: price questions (29.9%) → vague targets
  (13.4%) → game slang and typos (4.13%). Then listing the catalogue of item types
  pushed it *up* to 5.30% — **an enumeration meant to widen inclusion got used as
  grounds for exclusion**, and a companion "playing the game is NO" rule fired on
  the *words* `스킬`/`강화` inside legitimate item queries. Removing both gave
  **0.59% / 0.98% across two runs** (miss 1/38 both times). That second number is
  one query away from failing its own 1% bar — it passed, without margin, and
  that is worth knowing before trusting it. The false-rejection figure is
  **in-sample**: the rules came from reading this eval set's failures. The
  prompt's examples were invented to be absent from all 563 eval sentences.
- **A third round tried to fix that residual and everything it tried was
  rejected** (ADR-0039's 정정 section). Three things it overturned, all worth
  carrying: the recorded diagnosis was wrong (*"vagueness and off-topic squashed
  into one boolean"* fails to explain 2 of the 7 rejections — `장신구 시장이
  어떻게 될지` names a real category); `경계 2/16` was **the set being easy**, not
  the gate being good (the same shipped prompt cuts **5/32** once the boundary set
  is doubled); and the eval sets grew — boundary 16→32, out-of-domain 38→41, plus
  a **40-query held-out set**, with the originals frozen as `BOUNDARY_V1` /
  `OOD_V1_EXCLUDED` so the old denominators survive. The control reproduced its
  historical v1 numbers exactly (1/38, 2/16), which is what makes the rest
  readable. Three variants, ~2,990 calls, ~$0.25, **nothing shipped**.
- **The blind spot that round found is still open**: game-mechanics questions
  carrying item vocabulary (`강화 확률 올리는 방법 있어?`, `세트 효과 조건이 뭐야`)
  pass the gate — 4 of 5 held-out cases. The old out-of-domain set could not see
  this because its game-but-not-trading group (`보스`, `스킬트리`) contained **no
  item vocabulary at all**, so a gate keying on item words scored full marks.
  Severity is lower than the bug that created this ADR, and that is *because* of
  its own defences: search has no explanation LLM (ADR-0036) and the forecast
  prompt never sees the query and must name the item, so the failure is an
  **irrelevant answer, not a false one**. That was measured on the live deploy
  (2026-08-08), not just argued: of the 8 probes, **4 route to `faq_smalltalk`
  and are already declined correctly at 0 LLM calls**, one asks a clarifying
  question, one label was withdrawn as genuinely ambiguous, and **only 2 are
  real defects** — both `price_forecast`, both naming the real item they
  forecast, so no subject substitution. Two lasting points:
  - **A gate eval is not a pipeline eval.** `미검출 4/41` scores the gate on
    inputs it never receives in production, because the gate only sits on
    `item_search`/`price_forecast`. The fix is *not* to drop them from the
    denominator: routing was compared local vs deployed and only **6/8 agreed**
    — all 3 rules-decided ones reproduced, 2 of 5 classifier-decided ones did
    not. **"Does it reach the gate" is not a stable property of a query.** So
    print the branch beside each miss and read ADR-0039's coverage argument
    per-*branch* (every branch is covered), never per-query.
  - **The 2 remaining defects are ADR-0018's problem wearing a new hat.**
    `_forecast_branch` takes `run_search(size=1)`'s top hit however weakly it
    matched, so what's missing is a *relevance* judgment, not a topic one — and
    cross-encoder logits are only comparable within one query's candidate set.
    It looks simple enough to retry ("just cut low scores"), which is exactly
    why it is written down: reopening it needs per-query normalisation that is
    demonstrably comparable across queries, not a better threshold.

`_out_of_domain()` **takes no arguments**, so echoing the user's subject is
impossible by signature rather than by instruction — the same move as ADR-0036.
The response is not cached, and **only the first of ADR-0016's two reasons
applies**: the verdict is non-deterministic, so a false rejection would freeze for
the whole TTL. It does not go stale (`"삼성전자 주식"` is outside tomorrow too).
Don't read the two vetoes as one.

For the agent branch, MCP `search_items` returns **a sentence, not an empty
list** — an empty list cannot distinguish "we don't handle this" from "we handle
it but have no listings", and the model reads the second and answers anyway. Same
prescription as `estimate_note`. Note this path did **not** fire in the
end-to-end check: the agent declined from its own system prompt without calling
any tool (`llm_calls=1`), so the note had to be verified by calling the tool
directly. **A path that was never walked cannot be called working — and in an
agent this is unusually hard to notice, because the model can skip your code and
still answer correctly.** Check each defence layer for evidence that it ran;
here `llm_calls` was that evidence, and the answer text carried none.

The explanation prompts stopped receiving `query` in the same round. Measured on
the same 42 cases, the previously-shipping prompt scored **주어채택 1/9 and
대상명누락 9/9 on out-of-domain cases** — it would say *"현재 이 아이템의 최근
거래가는…"* without ever naming which item. Dropping `{query}` and requiring the
item's name scores 0 on both. On ordinary queries the two are identical, so this
is purely the second line of defence for when the gate misses.

Scope limit worth knowing: the zero-result handling does **not create** zero-hit
results, it only speaks correctly about ones that already happen. `"2만원 이하 전설 등급
무기"` is a zero-answer query that still returns 3 approximations, because
`전설 등급` is a *third* axis (`rarity`) that is **not the same free work** —
subcategory/element were readable off the descriptions, whereas grade doesn't
exist in the corpus and would have to be invented.

**Query-rewrite determinism is done** (ADR-0017), and the cause was mundane:
**`temperature` was never passed to the API**, so every call ran at OpenAI's
default of 1.0. Setting it to 0 took token-set mode agreement from 0.840 to
0.990 (unstable queries 5/10 → 1/10) with search quality unchanged. Two earlier
claims are corrected there: filter extraction was **already** stable at
temperature 1.0 (ADR-0016 reason 1 was inferred, not measured — the decision
stands on reason 2), and temperature does not explain the residual mis-extraction
(1/40 at both settings).

**Phase 8 is done** (ADR-0019…0027): observability stage 1, load testing, CI/CD
stage 1, id-space prefixing, JWT auth, rate limiting, and bundle splitting.
Three of those rounds ended in a **correction rather than a feature**, and each
correction was left as an inline block rather than a rewrite — see the roadmap's
마감 정리 for the list and for what was deliberately left unstarted (ELK/tracing,
`asyncio.to_thread`, real password auth), each with its trigger. The RAGAS gate
was on that list and **shipped in ADR-0043 — but not as a RAGAS gate** (below).

CI is four GitHub Actions workflows
(`.github/workflows/{ai,backend,frontend,ai-quality}.yml`), split into separate
files so each can carry its own `paths` filter — a docs-only commit runs nothing.
Four things about it are load-bearing:

- **The search-quality gate is `ai-quality.yml`, and RAGAS is not what gates it**
  (ADR-0043). Deterministic Recall@k/MRR carry pass/fail (no LLM calls, free); RAGAS
  is a `workflow_dispatch` report, because its judge variance was never characterised
  and a threshold on unmeasured noise is the mistake ADR-0028 and ADR-0040 already
  made twice. It also bills ~648 calls / **119 minutes** per run (measured). **ADR-0021's recorded blocker for this
  ("a model-artifact distribution strategy must come first") turned out to be two
  independent problems written as one** — retraining is ~20 steps so there is nothing
  to distribute, and the billing half vanishes once RAGAS is demoted. *A deferral
  reason bundled into one line looks bigger than it is; split it per item.*
  **The floors (recall@1 0.35 / recall@5 0.48 / mrr 0.47) came from measuring
  retraining variance first**, and measuring it correctly needed three arms: the seed
  is pinned at 42, so re-running twice yields **bitwise-identical weights** and a
  "variance" of 0 that is structural, not measured. Changing thread count gives
  *different weights but identical metrics to 4 decimals* — so the cross-machine axis
  is far smaller than the seed axis (the only real one: 0.0127–0.0370). **Print the
  artifact hash beside any repeated-run measurement**, or "same numbers" cannot be
  told from "same file". Scope is honest: this catches large regressions (corpus
  corruption, wrong base model, broken triplets), not subtle quality drift.
- **The load test is deliberately NOT in CI**, and the reason is not weight:
  a GH runner is a third environment whose numbers compare to neither ADR-0020
  nor the deployment target, there is no SLO to assert yet (ADR-0020 declined to
  set one), and `live-llm` bills OpenAI on every commit. The 4-OCPU worry is
  answered by **not self-hosting a runner**. Revisit only when an SLO exists, and
  then as `workflow_dispatch`, never a schedule.
- **The backend job brings up `docker-compose.yml` rather than GHA service
  containers.** Service containers cannot set `command`, so Redis can't run with
  `--requirepass`, and `RedissonConfig` unconditionally sends AUTH. Using compose
  also keeps CI identical to local. Only `postgres` and `redis` — the backend has
  no ES dependency. **It also does not bring up RabbitMQ, and the reason changed:**
  the backend gained an AMQP dependency in ADR-0030, but `NotificationFlowTest` is
  written to verify the publish *phase* and consumer idempotency **without a broker**
  (the spy records the call; the real send fails and is swallowed by fail-open).
  Green CI therefore says nothing about the broker path — `load/verify-mq.sh` is what
  covers queue drain, DLQ, and broker-down latency.

Note what the green badges do and don't mean. **This paragraph used to say the
backend suite was "still the single Initializr `contextLoads()`" — that went stale
when ADR-0030/0031/0034 added behavioural tests, and it contradicted this file's
own build section.** It is 57 tests now; what remains true is that
`contextLoads()` is worth running on its own, because context-startup failure is a
regression class that actually bit here (the Redisson/Boot-4 `RedisProperties`
relocation). Still absent: the Redis lock and the trade flow are curl-verified
only, and the load test is deliberately not in CI (see above).

Load-test facts worth carrying (`load/`, k6):

- **Overselling is 0 across ~26,600 concurrent purchases** — ADR-0001's claim
  finally verified under real concurrency. `load/run.sh` asserts
  `stock delta == 201 responses + 1 warmup` on every run; keep that assertion
  if you touch the harness.
- **The bottleneck moves with the load shape.** One contended item: 106 req/s,
  lock *wait* 0.178s vs *hold* 0.007s (queuing). Spread over 20 items: 430
  req/s, wait ~0, but hold rises 5× (the DB becomes the constraint). Measuring
  only the contended profile would have stopped at "the lock is the bottleneck".
- **LLM is 97% of AI search latency** (live p95 4.45s; ES + reranker + embedding
  together are 82ms). Always split `cache-warm` from `live-llm` — otherwise you
  are benchmarking OpenAI's rate limiter, not this system.
- **`seed-loadtest.sql` is a prerequisite, not an optimisation.** Demo items have
  `stock=10`, so any real load exhausts them in ~10 requests and you end up
  measuring rejections instead of lock contention.

Two things from ADR-0019 that change how you read this codebase:

- **`/api/assistant` used to discard the per-stage `timings`** that `run_search`
  and `forecast_price` already produced — only `execution_ms` survived. It now
  propagates them, and `explain_ms` (the second LLM call in the search branch)
  is measured for the first time. The instant payoff: a first request's 42.9s
  was **82% lazy embedding-model loading, not the LLM**. Any load test must warm
  up first or its early numbers are model-loading noise.
- **Metrics have exactly one instrumentation point**, `record_response()` in
  `app/core/metrics.py`. A new stage means one line in `_STAGE_BY_KEY`, not a
  new timer at the call site. Labels stop at `tenant`/`intent`/`stage`/`outcome`
  — **never label by `item_id`, `trade_id`, `user_id`, or query text**; that is
  a log question, not a metric one.

Prometheus and Grafana are a **`--profile observability` opt-in**, not default:
scraping during a load test steals CPU from the thing being measured on a shared
4 OCPU box. Read load-test aggregates by diffing `/metrics` before and after
(histograms are cumulative), and start the dashboards only for demos.

**The search-quality thread is closed for now** — the three
remaining items had prerequisites. **One is now done**: `"무속성"` extraction
(ADR-0040) — the prerequisite was a labelled eval set, and the fix turned out not
to be a prompt change at all. The `rarity` axis still needs a grade taxonomy
invented, and the reranker floor still needs cross-query score comparability
(see above). Anything that would have
followed floor adoption — notably splitting "no results matched your conditions"
from "results matched but scored too low" — **is moot**, since nothing cuts on
relevance now.

A methodological note worth carrying: repeating a procedure on the **same**
tune/holdout split measures noise, not generalisation. Enumerating splits over
the same collected data costs nothing extra (no new API calls) and is what
turned an apparently adoptable threshold into a rejected one. Do that before
adopting any threshold calibrated on a split.

### Infra
**Apps are containerized since ADR-0029, behind `--profile app`** — `docker compose
up -d` still means infra-only, and CI's backend job depends on that. `--profile app`
adds `backend`/`ai`/`web` plus two one-shot services: `ai-init` (builds the 5 models
into a volume, then seeds ES) and `db-seed` (applies `seed-demo.sql`, which **cannot**
run before the backend has booted once, since Hibernate creates the tables).
nginx reproduces the Vite dev proxy's path rules, which is why **CORS is still absent
from both servers** — `load/verify-container.sh` asserts that behaviourally, and its
first version was wrong (it probed a 404 path and passed even with CORS enabled).

Two consequences: **the 650MB of models live in a named volume, not the image**
(466MB of it is the XLM-R embedding matrix — 250k vocab × 384 dims — not waste), and
**behind the proxy the login IP rate limit used to collapse to one key** for the whole
deployment. That is fixed as of ADR-0033 — nginx now sets `X-Forwarded-For` and the
backend trusts it only from `rate-limit.trusted-proxies` (see the auth section below;
shipping only the trusting half is what made the limit *bypassable*).

**A public deploy uses `docker-compose.deploy.yml` on top** (ADR-0031): it publishes
**only nginx's 80**, sets `SPRING_PROFILES_ACTIVE=prod` (which arms `SecretGuard`,
refusing to boot on the repo's default secrets), and requires `restart backend web`
afterwards. Three traps live here, all invisible without actually running it:

- **`ports: []` does not remove ports** — Compose *appends* sequences. Use
  `!override []`.
- **`environment:` does not inherit your shell** — `RABBITMQ_HOST`, `DEMO_PASSWORD`,
  and `DB_PASSWORD` were each forgotten in turn, and the symptoms pointed at the
  application ("password authentication failed"), not at compose.
- **A closed port and a dead container look identical from outside.** The first run of
  `verify-deploy.sh` passed "8080 closed" while the backend had actually refused to
  boot. It now asserts the stack is running *first*, and ends with a real
  proxy-path login.

`docker-compose.yml` at the repo root brings up PostgreSQL, single-node
Elasticsearch, Redis, and RabbitMQ (see that file / `.env.example` for ports
and credentials). Elasticsearch is **built from `docker/elasticsearch/Dockerfile`**,
not pulled directly — it bakes in the `analysis-nori` Korean plugin, which
would otherwise be lost whenever the container is recreated.

**The deployment target is ARM, and nothing in the build branches on architecture**
(ADR-0032). All four images cross-build for `linux/arm64` and every dependency
resolves to an aarch64 wheel (`torch-2.13.0+cpu` from the CPU index — verified,
since `download.pytorch.org/whl/cpu` only gained `cp311` aarch64 wheels at 2.7.0 —
plus `onnxruntime-1.28.0`, numpy, scipy, tokenizers). Two things not to redo:

- **The reranker's int8 config stays `avx2` on ARM.** The name says x86 but the
  artifact is portable ONNX and was confirmed loading and inferring on emulated
  aarch64. Switching to `AutoQuantizationConfig.arm64` was measured and
  **rejected**: top-1 agreed 5/5 but **top-5 ordering agreed 0/5**, and every
  recorded quality number in this repo is avx2-based — it trades measured quality
  for an unmeasured speed guess. Revisit only if reranking is *shown* to be the
  ARM bottleneck, and re-run `evaluate_hard_filters` as part of the switch.
- **Cross-building is for confidence, not for artifacts** — build on the target,
  which is native. Use `--output type=cacheonly`; a 3GB emulated image on the dev
  box is pure cost, and its build cache is what filled the disk and killed the
  daemon (see the prune/compact gotcha above).

**Trade processing is synchronous on purpose; the queue carries what comes after**
(ADR-0030). The plan's flow was `lock → publish → consumer does the DB work`, which
would turn purchase into `202 Accepted` and break the shape of ADR-0020's
`stock delta == success responses` assertion. So RabbitMQ carries step 4 only —
post-trade notifications. Four things are load-bearing:

- **Publishing happens in `@TransactionalEventListener(AFTER_COMMIT)`.** Inside the
  transaction it would emit notifications for rolled-back trades. A test pins this,
  and the *first* version of that test was vacuous — `quantity=99` is rejected at the
  stock check, i.e. **before** the publish point, so it passed even with in-transaction
  publishing. The real test registers an event in a rolled-back `TransactionTemplate`.
- **Publish failure never fails the trade** (fail-open, same family as the AI rate
  limiter). But fail-open has a *latency* axis too: publishing runs on the request
  thread after commit, and the AMQP client's default connect timeout is 60s — left
  alone, a dead broker means "purchase succeeds but takes a minute". Hence
  `connection-timeout: 2s` and template retry off.
- **Idempotency is `(recipient_id, trade_id)` unique — and the catch must be
  `DuplicateKeyException`, not `DataIntegrityViolationException`.** The parent type
  also swallows FK violations, so a message naming a nonexistent user would vanish as
  "already handled" instead of reaching the DLQ.
- **Counting notifications right after load is a false-failure trap.** Consumption is
  async; wait until `messages_ready` **and** `messages_unacknowledged` are both 0
  (`load/verify-mq.sh`), and treat the timeout as a failure, not a pass.
- **A new infra dependency needs its failure policy set in three places, not one** —
  the call site (swallow or propagate), `depends_on`, and **the health check**. Miss
  the last one and the first two are moot: Spring auto-registers a `rabbit` health
  indicator and `/actuator/health` ANDs them, so a dead broker made the whole instance
  `unhealthy` — trading and search included — while the code was carefully fail-open.
  Hence `management.health.rabbit.enabled: false`; publish failures are a *metric*
  (`trade_event_published_total{outcome="failed"}`), not a health verdict. The same
  round also shipped without `RABBITMQ_HOST` in compose, because **`localhost` is the
  correct value locally** and only wrong inside a container. Both defects are invisible
  to the local dev loop by construction —
  `docs/05-Troubleshooting/로컬-프로세스로는-볼-수-없는-결함.md` lists what that loop
  cannot see. **Close a round's bars only after running it containerized.**

### backend/ (Spring Boot 4.x, Java 21, Gradle)
Package layout: `domain` (entities per aggregate: `tenant`, `user`, `item`,
`trade`, plus `domain.common.BaseTimeEntity`), `repository`, `service`,
`controller`, `dto`, `config`, `client`, `exception`. Every entity table
carries `tenant_id` from the start. Implemented: Item CRUD, Redis-lock-backed
purchase/bid (`ItemController`, `TradeController`), login (`AuthController`),
notifications (`NotificationController`, incl. `PATCH /read`), my trade history
(`TradeHistoryController`, ADR-0037), and `GET /api/health` which proxies a health
check to the FastAPI server.

Build/run from `backend/`: `./gradlew build`, `./gradlew bootRun` (port 8080).
No lint task configured yet. The suite is **57 tests** across `DomainRuleTest`,
`NotificationFlowTest`, `AuthenticationTest`, `LoginTest`, `MyDataScopeTest`, and
the Initializr `contextLoads()`; it needs the live Postgres/Redis from
docker-compose. **`./gradlew test` alone can report BUILD SUCCESSFUL having run
nothing** (`:test UP-TO-DATE`) — use `--rerun-tasks`. The Redis lock and the trade
flow are still curl-verified only; see the roadmap's 기술 부채 section.

### ai/ (FastAPI, Python 3.11)
- `app/routers/` — health, llm, search, forecast, anomaly, **assistant**
  (`POST /api/assistant` is the unified entry point; the per-capability
  endpoints stay because the MCP tools wrap them). **`POST /api/assistant/stream`
  is the same work with progress events** (ADR-0044) — the non-streaming route
  stays because `load/k6/ai-search.js` and `verify-container.sh` are wired to it.
  Three things about it are load-bearing:
  - **It streams stages, not tokens.** A compound query's 7-25s is mostly tool
    calls, so token streaming would only make the last 1-2s look faster. And it
    would mean replacing `LLMClient.chat()` with a streaming API, i.e. rewriting
    both provider translations (ADR-0042); a stage callback is one line at each
    existing timing point.
  - **A `tool` event fires when the call *starts*, not when it finishes**, and
    that was a measured correction: emitting only on completion left the biggest
    wait mislabelled — 11.1s of tool execution displayed as "choosing a tool".
    The response's own `agent_tool_ms` said so. Progress means "what is happening
    now", and **which stage dominates flips with warmth** (cold: tools 11.1s;
    warm: LLM 3.6s).
  - **A cache hit is wrapped in the stream too**, emitting `cache` then `done`
    immediately. Returning a plain response on hits would give the client two
    code paths to save 25.9ms.
  - **The deploy has TWO proxies, and `X-Accel-Buffering: no` alone was not
    enough.** nginx honours that header *and hides it* — `Date`, `Server`,
    `X-Pad` and `X-Accel-*` are its default hidden list. The public URL is
    `https://` while `gimp-web` serves plain 80, so a TLS-terminating host nginx
    sits in front; our nginx consumed the header and the front one never saw it,
    so it buffered (measured: all 8 events in the last 0.27s of 13.98s).
    `proxy_pass_header X-Accel-Buffering;` fixes it — honouring and forwarding
    are separate. **Verified at two points in the chain**: direct to our nginx
    the header is *present* (ratio 0.01), through the public URL it is *absent*
    (ratio 0.02) — and the absence is the evidence the front proxy consumed it.
    *A header a proxy consumes cannot be checked in the response; trace it by
    where it disappears.* The check that asserted its presence was therefore
    **unsatisfiable behind a proxy** and is now informational; the ratio carries
    the verdict. The front proxy's config is outside this repo, so
    `load/verify-sse.sh` is the only thing that would catch a regression there.
  `app/core/progress.py` holds the callback type — it is in `core/` and not
  `assistant/` because `agent` uses it too, and `assistant/` would make
  `pipeline → agent → assistant` a cycle.
- `app/services/llm/` — `LLMClient` ABC + `OpenAIClient`. `chat(messages,
  tools)` is the abstract method; `complete(prompt)` is a concrete wrapper on
  top of it, so tool-calling was added without touching any existing caller.
- `app/services/router/` — rules (regex, abstains when unsure), classifier
  (KoELECTRA-small, lazy), router (rules → classifier → COMPOUND on low
  confidence). See ADR-0010.
- `app/services/mcp/` — server (3 tools wrapping existing pipelines), session
  (**in-memory** transport + OpenAI schema conversion), `__main__` (stdio entry
  point for external MCP clients). See ADR-0011.
- `app/services/agent/` — sequential tool-calling loop, max 5 steps. Tool
  failures come back as results, never exceptions.
- `app/services/cache/` — semantic_cache (Redis + numpy cosine), policy
  (per-intent TTL and semantic gating). See ADR-0012.
- `app/services/assistant/` — orchestration: cache → route → branch → store.
  Branches escalate to the agent when they can't resolve a required id. An empty
  `ITEM_SEARCH` result short-circuits to `_no_results()` (deterministic answer,
  no explanation LLM call, not cached — ADR-0016).
- `app/services/search/` — mapping, embedding, es_client, filters, **domain_gate**, hybrid
  (BM25+kNN via one `_msearch`, app-side RRF), query_understanding, reranker,
  indexer, pipeline
- `app/services/training/` — hard_negatives (triplet mining), evaluation
  (Recall@k / MRR)
- `app/services/forecast/` — dataset (window/ratio normalization), model
  (`PriceLSTM`), predictor (lazy load), cold_start (ES donor search + weighted
  trend inheritance), evaluation (MAPE + naive baselines + signal correlation),
  pipeline. One **global** model across all items, not per-item — see ADR-0008.
- `app/services/anomaly/` — features (11 context-relative axes + RobustScaler),
  dataset (**three-way** normal split: train / threshold-holdout / eval),
  scenarios (behavioral anomaly injection), model (`TradeAutoencoder` 11-8-4),
  detector (scoring + per-feature contribution), evaluation (PR-AUC + rule
  baselines), pipeline. See ADR-0009.
- `app/corpus/` — **`TRAIN_ITEMS` (24) and `EVAL_ITEMS` (18) are strictly
  disjoint**, and **every item must carry every field in
  `HARD_FILTER_FIELDS` (`subcategory`, `element`)** — a missing one makes the
  item silently invisible to any search filtered on that field, so `__init__.py`
  asserts both at import time. `element` is the subtler one: an item with no
  element still needs the *value* `"무속성"`, because the filter's `None` means
  "don't filter" while `"무속성"` means "only items with no element". Never
  generate
  training data from `EVAL_ITEMS` — it is the held-out set the Phase 4 numbers
  depend on. `trade_history.py` holds the Phase 5 dummy trade series (seeded
  generator, 19 items × 120 days + 4 deliberately Cold-Start items).
  `trades.py` expands that into ~26k individual trades plus a 150-user pool
  with **deliberate repeat-counterparty structure** — without it,
  `pair_trades_7d` alone separates the composite anomaly and the whole
  interaction test collapses.
  `out_of_domain.py` holds the **hand-written** domain-gate eval — 38 out-of-domain
  queries **and** 16 in-domain ones chosen to *look* out-of-domain. Both lists are
  required: a one-sided set makes the opposite extreme score perfectly, and a set
  of obvious in-domain queries would report 0% false rejection because the sample
  was easy, not because the gate is safe. Keep the game-adjacent-but-not-trading
  group (`"이 보스 어떻게 잡아?"`) — without it a gate that passes anything
  containing a game word scores full marks.
  `element_queries.py` holds the **hand-written, answer-labelled** element eval
  (39 queries in five groups: 무속성 / 타속성 / **미언급** / 저항 / 부정형).
  The `미언급` group is the load-bearing one — without it, "fill 무속성
  everywhere" scores perfectly. It also deliberately contains one phrasing the
  implementation does *not* handle (`"속성 안 붙은 신발"`), because an eval set
  everything passes cannot show a limit.
  `intent_utterances.py` holds the **hand-written** router eval + boundary sets
  (LLM-generated training utterances live in `data/intent_train.json`);
  `cache_pairs.py` holds the synonym/trap pairs for cache threshold work.
- `scripts/` — seed_items, build_reranker_onnx, generate_hard_negatives,
  generate_eval_queries, finetune_embedding, evaluate_embedding,
  compare_eval_sets, train_forecast, train_anomaly, generate_intent_data,
  train_intent_router, evaluate_semantic_cache, evaluate_rerank_floor,
  evaluate_hard_filters, evaluate_rewrite_determinism, evaluate_explanation_prompts,
  evaluate_domain_gate, evaluate_element_extraction, benchmark_cpu_stages,
  evaluate_training_variance, check_ir_gate.
  **The last two are the CI gate's two halves and must stay split** (ADR-0043):
  `evaluate_training_variance` derives the thresholds (train 7×, LLM 0×) and
  `check_ir_gate` applies them to whatever `evaluate_embedding --json` wrote — so
  changing a threshold costs no retraining, the same collect/score split the prompt
  harnesses use. `evaluate_embedding` **exits 1 when the fine-tuned model is
  missing**; it used to print advice and exit 0, which CI would have read as a pass.
  **`evaluate_rerank_floor` and
  `evaluate_hard_filters` answer different questions and must stay
  separate**: `evaluate_rerank_floor` documents a *rejected* approach (it sweeps
  thresholds and would print a recommendation), while `evaluate_hard_filters`
  measures how many unfit results a threshold-free filter leaves. It A/Bs within
  a single run — comparing against a previous run's aggregate lets query-rewrite
  drift contaminate the delta. Its `is_fit()` labels off the item **name**, never
  the filtered field, or measuring the filter becomes circular.
- `data/` — generated triplets and eval query sets (tracked; small and worth
  versioning for reproducibility). `models/` is gitignored — regenerate with
  the build/finetune scripts. **`training_variance.json` is tracked and
  `/ir_metrics.json` is not**, and the split is the point: the first is the
  *derivation* of `check_ir_gate`'s thresholds (delete it and the numbers become
  unverifiable), the second is a per-run output that CI ships as an artifact.

- `tests/` — `python -m pytest` from `ai/` (**292 tests**, no `pytest-asyncio` — the
  few async cases use `asyncio.run`). Deliberately limited to
  deterministic units: RRF fusion, router rules, cache keys/tenant isolation,
  per-intent TTL + the no-result storage veto, id-space guards, filter→DSL
  conversion, explanation-prompt guards (ADR-0038, plus "neither prompt sees the
  query" — ADR-0039), **both** search answers (empty and found — the found one asserts it
  never denies results that exist, ADR-0036), route auth coverage, temperature
  plumbing, and the **domain gate's wiring** (ADR-0039 — the verdict itself is an
  LLM call and is measured by `evaluate_domain_gate.py`, not here; what the tests
  pin is that search short-circuits, that `NO` is matched at the front rather than
  as a substring, and that `in_domain` has not crept back into the extraction
  prompt). Newer additions keep the same shape — all of them run **without a
  network**, which is possible precisely because each round pushed the decidable
  part into code: `fill_missing_element` (ADR-0040), the degradation fallbacks and
  their `degraded` outcome (ADR-0041), a source scan proving **no router
  interpolates the exception into a 500 body**, and the whole OpenAI→Anthropic
  **message-format translation** plus circuit-breaker behaviour (ADR-0042). The
  live provider round-trip was walked once by hand and recorded in the ADR, not
  pinned here. `test_ir_gate.py` (ADR-0043) is the newest and follows the same
  rule for a check that only ever runs in CI: **every passing case is paired with
  a failing one**, including the vacuity guards (same base/tuned path, all-identical
  metrics) that the "compared the tuned model against itself" defect would trip.
  Model quality itself is still judged by the training scripts' held-out reports —
  what CI gates is that the reports did not collapse (ADR-0043's floors), not that
  they improved subtly. Everything else is manually verified (see the
  roadmap's 기술 부채 section).

Setup: `python -m venv .venv && .venv/Scripts/python -m pip install -r
requirements.txt`, then `python -m uvicorn app.main:app --port 8000`.
Seed search data with `python -m scripts.seed_items --recreate` (indexes
train + eval items).

### frontend/ (Vite + React + TypeScript)
`npm install` then `npm run dev` (port 5173). **A Vite dev proxy makes the
browser see one origin** — `/api/backend/*` → 8080, `/api/ai/*` → 8000 — which
is why neither server has CORS configured; don't add it. Screens: `/`
(assistant + search; **with no `?q=` it shows the item browser** — sortable,
server-paged, ADR-0037), `/items/:id` (detail + price chart + purchase/bid),
`/trades` (my trade history), `/notifications`, `/anomalies` (GM queue). State is
TanStack Query only; there is no global store.

**The browse table has six columns because Postgres `items` only has six worth
showing.** `subcategory`/`element`/`enhancement_level`/`required_level` live in
the ES mapping only, and `export_demo_sql.py` states the split as a decision
("화면을 위해 백엔드 스키마를 늘리지 않는다"). Don't overturn it for a screen —
and note `seed-demo.sql` opens with `DELETE FROM trades`/`notifications`, so
re-seeding to add columns **destroys real trade history**. The live consequence
is a known inconsistency: a search card shows `검 · +9 · 60렙` and the detail page
it links to does not. Fixing that needs either a schema change or the detail page
reading ES too — both are their own decision.

**No TanStack Table, no Tailwind/shadcn — deliberately.** Sorting and paging are
server-side (`Pageable`), which is exactly what that library does *not* help with;
the whole browse screen cost **2.9KB** of bundle. Tailwind would mean rewriting
all five screens' styles for zero functional gain. Reach for them when
client-side interaction (filtering, virtualization) actually exists.

**The search query lives in the URL (`/?q=…`), not in `useState`** (ADR-0037).
Going into an item and back used to remount `Assistant` and lose the results —
local state dies with the unmount and a `useMutation` result is never cached.
Changing the back link alone does not fix that; the query has to survive the
remount. With it in the URL, `useQuery` keyed on `["assistant", q]` serves the
cached answer instantly and searches become shareable. **Watch `isPending` vs
`isFetching`**: in TanStack Query v5 a query with `enabled: false` sits at status
`pending` forever, so `isPending` locks the button into "처리 중…" before anything
is typed. **Do not give that query a `staleTime`** — a 5-minute one was tried and
made the badge row lie: re-asking a query replayed the first response, whose
`cache: {hit:false}` then showed forever. The badges are instrumentation about the
server (cache hit, `llm_calls`, routing verdict); caching them client-side turns
instrumentation into an afterimage. The general rule: **a response that carries
observations is not cacheable on the client**, because *when it was taken* is part
of what it means. Cost is not the argument against it — a server cache hit is 0
LLM calls at p95 25.9ms — and `staleTime: 0` still paints cached data instantly
while refetching, so back-navigation keeps its results either way.

**`logout()` calls `queryClient.clear()`, and that is the load-bearing line.**
No query key carries the user (`["notifications","unread"]`, `["trades"]`,
`["alerts"]`), so clearing only the token leaves the previous account's responses
on screen until each refetch lands. Per-user keys would be more precise but are
**forgettable on every new query**, and the symptom of forgetting is quiet. "A
finished session invalidates all server state" has nowhere to forget.

**Authentication is real since ADR-0031** — `POST /api/auth/login
{tenantCode, username, password}` with BCrypt. **The credential is three parts, not
two** (ADR-0034): `users` is unique on `(tenant_id, username)`, so a username alone
does not identify a row. It used to be looked up by username only, with a repository
comment asserting "usernames are globally unique because there is one demo tenant" —
a claim the schema never made. Adding a second tenant with the same username produced
`NonUniqueResultException`, which surfaced as **401**, i.e. that account became
permanently unable to log in and the symptom said "wrong password". Login is the one
place a request may carry tenant, because it is the credential-presentation step;
everything after it still reads tenant from the token only. A missing `tenantCode` is
**400**, an unknown one is **401** (a 404 would leak the tenant list). `demo-token` is **gone**, and a test asserts
it 404s *while holding a valid token* (a plain 401 would only prove the security
layer fired, not that the handler is absent). There is **no signup**: accounts are
seeded and fixed, deliberately, because opening registration drags in email
verification, password reset, and spam-account defence.

Three things about it are load-bearing:

- **Passwords never live in the repo.** `seed-demo.sql` is generated and committed,
  so its hash is a 29-char placeholder that matches nothing;
  `DemoAccountInitializer` injects the real ones from `DEMO_PASSWORD` /
  `ADMIN_PASSWORD` at startup. Forgetting them fails **closed** — nobody can log in.
- **The GM password is separate on purpose.** One shared password would mean anyone
  who knows the demo password is a GM, making ADR-0023's role check meaningless.
  `LoginTest` checks this **both ways** — the reverse direction (admin password
  against every ordinary account) is what catches an initializer that assigned
  them backwards.
- **`db-seed` overwrites the injected passwords**, because it runs after the backend
  is healthy (Hibernate must create the tables first). nginx separately caches the
  backend's IP at startup. Both are fixed by `restart backend web` after `up`, and
  `load/verify-deploy.sh` fails with 401 or 502 if that step is skipped.

**The session lives in `sessionStorage` since ADR-0036**, not in memory. ADR-0031
kept it in memory so a refresh logged you out — right about `localStorage`, but it
was never a binary choice. Under XSS the in-memory token is taken from the page
anyway; what `sessionStorage` still avoids is surviving a closed tab and being
shared across tabs. **Three places end a session** (logout, expiry, restore
failure) and all three must clear the key — clearing React state alone leaves an
expired token that gets restored on every reload.

Three consequences worth carrying:

- **Nothing sends tenant or actor in the request any more.** Both come from
  signed claims, and the `X-Tenant-Id` / `X-User-Id` headers are gone — sending
  them now does nothing. The backend reads `tenant_id` (Long) and the AI server
  reads `tenant_code` (str) from the *same* token, so `src/demo.ts` no longer
  absorbs that difference; its constants are display-only.
- **`tenant_code` was removed from every AI request body/query** for the reason
  ADR-0022 gives: two sources for one fact can disagree undetectably. It also
  closed a real hole — semantic-cache keys are per-tenant, so a caller used to be
  able to ask for another tenant's cache.
- **`JWT_SECRET` must be identical in two places** (repo-root `.env` and
  `ai/.env`), unlike `OPENAI_API_KEY` which lives only in `ai/.env`. A mismatch
  is confusing rather than obvious: issuing still succeeds and only the AI server
  returns 401.

Auth costs **0.7ms per request** (measured via `spring_security_filterchains_seconds`,
0.22% of a 312ms request) — if throughput looks off, it is not this. See ADR-0023
for two throughput hypotheses that were tested and rejected.

**Rate limits exist too** (ADR-0024), on the paths that cost money or are
reachable without a token: `/api/assistant` (20/min per tenant+user),
purchase/bid (10/s per user), and `/api/auth/demo-token` (30/min **per IP** — the
one place keyed by address, since it runs before identity exists). No new
dependency: the backend reuses Redisson's `RRateLimiter`, the AI server uses the
`redis.asyncio` client the semantic cache already opens.

**The two limits count different things, and only one counts cache hits**
(ADR-0044). The per-minute limit is a *load* limit and runs as a dependency, so it
counts hits. The daily cap is a *cost* limit and is now consumed **after** the
response, and **not at all on a cache hit** — a request that cost 0 LLM calls was
eating the budget whose stated ceiling is the OpenAI monthly cap. This was reachable
in normal use, not theoretical: ADR-0037 deliberately left `staleTime` off so the
badges stay honest, so **every back-navigation from an item page refetches** and used
to spend one of 50. Three things follow:
- **Both routes must go through `_ask_metered()`.** If the rule forks, the streaming
  path *is* the bypass; `test_assistant_stream.py` scans that neither handler calls
  `ask()` directly.
- **A failed request still consumes.** By then the LLM call may already have gone out,
  and free failures are their own bypass.
- **Two places can silently delete the limit**: check and consume must use the *same*
  `_daily_key()` (otherwise the check reads 0 forever while the counter climbs
  unwatched), and `_peek` compares `<` where `_hit` compares `<=` because it looks
  *before* incrementing — copying the operator across raises the cap by one.
Verified live against Redis with **both arms**: misses drove the counter 0→1→2, hits
left it at 2. The first attempt measured hits only and read 0 — which is
indistinguishable from "the increment is broken".

**Since ADR-0031 there is also a daily cap** (50/day per user) on `/api/assistant`,
keyed with the **KST date inside the key string** so it resets at a time you can
explain ("midnight KST") rather than at each user's first call. It accepts the
fixed-window 2× boundary at day scale — the real ceiling is the OpenAI monthly cap,
and this layer targets ordinary abuse, not adversarial precision. **A request
rejected by the per-minute limit must not consume the daily budget** (increment only
after the first check passes). **The daily key's TTL is seconds-until-midnight, not
86,400** — `_hit()` returns the remaining TTL and the handler ships it as
`Retry-After`, so a 24-hour TTL told a user who first asked at 01:00 and hit the cap
at 23:00 to come back in *22 hours* when the real answer was one. The TTL had been
dismissed as housekeeping because the key's *date* decides the reset; **a value you
treat as internal is worth checking for leaks into observable output.**
`X-Forwarded-For` is read too — but **only from
addresses in `rate-limit.trusted-proxies`, which defaults to empty**, so an
unconfigured deployment behaves exactly as before instead of trusting a spoofable
header.

**Trusting a header and writing it are two separate jobs, and this repo shipped only
the first** (ADR-0033). `nginx.conf` had no `X-Forwarded-For` line at all, and nginx
does not strip headers it doesn't set — so a client's forged XFF reached the backend
untouched, and following the runbook's "put nginx's IP in `TRUSTED_PROXIES`" step
**removed the login limit entirely** (measured: 40 forged values all passed; control
with no header hit 429 on the 31st). Fixed by
`proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;` — whose appended last
element is exactly what `clientIp()` already read. Two lasting rules:

- **`verify-auth.sh`'s XFF check now goes through the proxy**, because that is the
  path that ships. Its previous version hit the backend port directly and justified
  that with "nginx overwrites the header anyway" — **an unverified claim about the
  very config that was wrong**. A check aimed at a path you do not deploy tells you
  nothing about the one you do.
- **`/api/llm/test` had no auth dependency and was reachable at `/api/ai/llm/test`** —
  an open, unmetered OpenAI relay that bypassed all three cost layers. The router is
  deleted, and `ai/tests/test_route_auth_coverage.py` now pins that **no route lacks
  an auth dependency**, since per-route `Depends` fails silently when forgotten.

**`/error` must stay in `PUBLIC_PATHS`** (ADR-0034). Spring Security 6+ filters the
ERROR dispatch, so without it Boot's error forward hits `anyRequest().authenticated()`
and **every 400 and 500 on an unauthenticated path comes back as 401**. That single
omission disguised two real defects as "wrong password" — the duplicate-username case
above and a `JWT_SECRET` shorter than 256 bits. The control that proves the mechanism:
an *authenticated* request to a missing path correctly returns 404. Nothing leaks,
because `server.error.include-stacktrace` defaults to `never`.

**A short `JWT_SECRET` is rejected in `SecurityConfig`'s constructor, not in
`SecretGuard`.** The old comment there claimed `SecretKeySpec` fails fast on a short
key; it does not (only an empty array is refused), and Nimbus raises
`KeyLengthException` at the *first login* instead. Measured: a 5-byte secret boots
**healthy**, passes the health check, and only login fails — as a 401. `SecretGuard`
is the wrong home for this because it is `prod`-only and asks "is this the repo
default"; a short key is broken in every profile.

- **All three cost layers count requests, not cost — so request *size* is bounded
  separately** (ADR-0035). `query` had no `max_length` and nothing truncated it:
  a 19,800-char query returned **200** in 16.4s (a normal one is ~50 chars / 4.5s),
  which multiplies the daily budget while staying inside the daily cap. Now
  `min_length=1, max_length=500` on both `/api/assistant` and `/api/search`. The
  number is derived, not guessed: the embedding truncates at **128 tokens** and the
  reranker at **256**, so text past that only inflates the LLM bill — and the 547
  queries in `data/` top out at **33 characters**. The give-away was in the same
  class: `size` was already `ge=1, le=50` while `query` was unbounded. **When one
  field in a DTO is bounded and its neighbour isn't, the unbounded one is an
  omission, not a decision.**
- **Load tests trip these limits**, so both servers need the relaxed config:
  `SPRING_PROFILES_ACTIVE=loadtest` and `RATE_LIMIT_ASSISTANT_PER_MIN=…`. The
  relaxed values deliberately live *outside* `application.yml` so they cannot be
  left behind, and limits are **raised, never disabled** — the limiter must still
  run so `run.sh` can assert the run wasn't contaminated.
- **Never build a check on rendered output.** `load/out/*.diff.txt` once ranked
  all counters together by absolute delta and cut at 25, so byte counters buried
  a 3,057-request delta and the contamination warning passed silently on a run
  that was 90% rejected. The display is fixed (ADR-0025 splits by unit and folds
  equal deltas) but **the check still reads the `before`/`after` snapshots**, and
  should stay that way — a display exists for humans and its rules will change
  again. This is the third of four times a check here was itself wrong; the
  pattern and its checklist are in
  `docs/05-Troubleshooting/검사-자체가-틀린-사례들.md` — **27 cases now**, and
  seven of them came from a single day of building new measurement tools. Two
  share one cause worth naming: **Korean draws its distinctions in the verb
  ending, but regexes are easiest to write against nouns**, so `이상 거래` also
  matched `이상 거래로 판별되지 않았습니다` and `거래를 고려` also matched
  `거래를 고려하실 때 참고하시기 바랍니다`. Both were caught by reading the
  flagged samples, never by the numbers. **The same failure then recurred in a
  prompt** (ADR-0039): a "playing the game is out of scope" rule fired on the
  words `스킬`/`강화` inside legitimate item queries. Corollary: **run any new
  check against a deliberately failing case too** — a check only ever seen
  passing is indistinguishable from one that always passes.
  - **And then read the result correctly** — case 26 is the first time a
    deliberately-failing case was built right and *read* wrong. `cmd | tail`
    followed by `$?` reads **`tail`'s** exit code, which is always 0, so two
    guards that were correctly exiting 1 both reported success. The filter was
    added only to tidy cp949-mangled Korean output. **Never put a display filter
    on the command whose exit code is the verdict** (`${PIPESTATUS[0]}`,
    `set -o pipefail`, or redirect to a file and read it separately). Same family
    as the `grep … | head -1` that invented a Cache-Control policy. Note this
    class **cannot happen in a CI `run:` step**, so a hand check is weaker than
    the workflow here, not stronger.
- **When something pins reproducibility, repetition measures nothing.** Training
  runs at a fixed `seed=42`, so "run it twice and compare" yields **bitwise
  identical weights** — a variance of 0 that is structural, not measured, and
  indistinguishable from a stable process. Vary the thing that is actually pinned
  (seed), keep a same-seed pair as the control, and **print the artifact's hash
  next to every run**: without it, "the metrics matched" cannot be told from "it
  was the same file". Doing this surfaced a genuinely useful fact — different
  thread counts produce *different weights but identical metrics*, so the
  cross-machine axis is far smaller than the seed axis (ADR-0043). This is the
  discrete cousin of "repeating a procedure on the same tune/holdout split
  measures noise, not generalisation".
- **A failed call is missing data, not an answer.** Case 19 is the first where
  one defect produced a false alarm and a silent pass *at the same time*: the
  collector returned `in_domain: None` on error, one metric counted it via
  `is not False` (so 443 rate-limit failures became a 63.2% "miss rate") and its
  neighbour via `is False` (so the same failures made false-rejection look
  *better*). Drop errors from the denominator **and refuse to score at all** past
  a small error rate — dropping alone leaves half the sample gone behind a
  plausible number. It was caught only because the collector printed per-set
  failure counts, which is the same "put every value the verdict used into the
  output" rule paying off again.
- **`curl` always asks the server; a browser may not.** Every check in
  `verify-container.sh` passed (14/14), the deployed image's bundle contained the
  new code, and the screen still showed the old UI — the browser was serving both
  `index.html` and the old hashed JS from its own cache. `frontend/nginx.conf` gave
  `/assets/` a correct `immutable` year, but **`index.html` had no directive at
  all, and "no directive" means browser heuristic caching, not "don't cache"** —
  and index.html is the only thing that names the hashed bundle. It now sends
  `Cache-Control: no-cache` (revalidate, usually a 304), and 판정 3-b asserts
  **both** halves: no-cache on the HTML and `immutable` on an asset path scraped
  out of that HTML. Checking only the first would pass a config that turned all
  caching off. **That check then misfired on its own first run**: nginx's
  `expires` and `add_header` each emit their own `Cache-Control` line, so the
  response carries two, and `grep … | head -1` read only `max-age=31536000` and
  missed `immutable`. Repeated headers are equivalent to one comma-joined value —
  **`head -1` silently invents a policy that has no basis.** Join them.
- **Read the HTTP status before parsing the body.** An error body has none of the
  fields you are looking for, and "field absent" usually looks identical to one
  meaningful value: a 429 on `/api/assistant` yields no `out_of_domain` and no
  `results`, which is exactly the signature of "the gate rejected this" — so the
  new check reported *"the gate is blocking legitimate searches"* when the truth
  was a spent quota. `verify-container.sh`'s login check had already learned this
  and says so in a comment; the lesson was not carried to the check added beside
  it. **When adding a check, read what the neighbouring checks already know.**
  Related operational fact: that script spends **9 of the 50 daily `/api/assistant`
  calls per run**, so roughly five runs exhaust a user's day.
- **A one-sided metric makes the opposite extreme optimal.** Measure rejection
  only and "reject everything" scores perfectly; measure pass-through only and
  "pass everything" does. The domain-gate eval carries a hand-written
  out-of-domain set *and* an in-domain set whose members deliberately look
  out-of-domain, for exactly this reason.
- **Measure the instrument's idle floor before you set a threshold on it, and
  never set one on `max`.** ADR-0028 pre-registered "ticker max < 20ms", then
  got 32.75ms and 17.89ms from the *same code* — because an idle server with
  zero load already hits 16.34ms (Windows timer resolution), leaving the bar
  3.66ms of headroom, and `max` is decided by one sample out of 3,600. The bar
  was left failed rather than rewritten after the fact; the adoption case rests
  on p99 and the over-threshold count, which agreed across both runs. General
  rule: a threshold whose derivation never mentions the noise floor has no
  derivation, and **any bar worth trusting must be run twice**.
  - **The discrete version of that bit too** (ADR-0039's correction, case 22): a
    pre-registered bar of `2/16 → ≤1/16` is **indistinguishable from one coin
    flip** — exact 95% intervals are [1.6%, 38.3%] and [0.2%, 30.2%]. Registering
    a bar before seeing results does not make it *measurable*; those are separate
    questions, and the second one is only ever caught in **measurement-design
    review, not code review**. Print the interval next to every small-sample rate.
- **Adding an example or a clause to a prompt can narrow the clause it was meant
  to widen** — three times now (ADR-0039). Listing the catalogue cut names off the
  list; "playing the game is NO" fired on the words `스킬`/`강화`; and adding
  *"budget-only phrasing counts too"* to the vagueness clause made the model read
  that clause as **requiring** a budget, so `추천 좀 해줘` started getting cut.
  To widen a clause, **try removing examples first** and measure that.
  - Related: **reframing what the judgment is about changes the sentence's
    grammatical subject.** Asking "is the *subject* something sellable" rejected
    `활 가격에 대한 전망은 어때?`, because the subject of a price question is
    `가격`, not the item. In Korean the topic and the subject often differ; check
    price-form queries first whenever the gate prompt is touched.
- **A held-out set is necessary but not sufficient.** ADR-0039's variant C passed
  *every* held-out bar and was still rejected — the regression it caused
  (pure-vague queries) lived in a class deliberately left out of the held-out set
  for having ambiguous labels. **Whatever you exclude is what the held-out set
  cannot see.** Score both: held-out answers "is the targeted gain real", the
  in-sample set answers "what broke paying for it". Two more rules from that
  round: **use a held-out set once** — reading it and then editing the prompt
  turns it into a second tune set — and **adoption and rejection are asymmetric**,
  since two agreeing runs are needed to ship a change but one clean failure is
  enough not to, the default being no change.
- **OpenAI failure now falls over to Claude, then to a deterministic sentence**
  (ADR-0042 in front of ADR-0041). Four things worth carrying:
  - **Most of `anthropic_client.py` is format translation, and that is the
    risk.** `LLMClient` is neutral but the agent loop speaks OpenAI shapes
    (`{"role": "tool", "tool_call_id"}`), so matching only the interface
    leaves `complete()` working while **the agent breaks silently** — exactly
    when the fallback is needed. Consecutive tool results must be merged into
    one user message; skip that and it works with one tool and 400s with two.
  - **The breaker exists for a latency reason, not a correctness one.** Plain
    try/except means every request during an outage pays the primary's
    timeout first — the same axis that made a dead broker turn purchases into
    60-second successes (ADR-0030). No new dependency: a counter and a
    timestamp, like the rate limiters reusing what each side already had.
  - **When both fail, the *primary* exception is raised.** Re-raising the
    secondary's would point diagnosis at the wrong provider.
  - **No key means no wrapper**, logged at startup with the file to fix —
    wrapping a fallback that cannot work hides its absence until an outage.
    The fallback runs at the same `temperature`; a shakier fallback makes
    answers *worse* exactly when things are already broken.
- The AI limiter **fails open** when Redis is down (it protects cost, not
  correctness). The purchase lock is the opposite and rejects — same Redis,
  deliberately opposite policies.
- **The explanation LLM failing is no longer a 500** (ADR-0041). Before it was,
  and the asymmetry was accidental: search survives an OpenAI outage because
  `understand_query` falls back to the raw query and the domain gate fails open,
  while forecast/anomaly died — **even though the answer was already computed by
  then**. Only the prose was missing. They now fall back to deterministic
  sentences built the same way `_search_answer()` builds its (ADR-0036); that
  makes `llm_calls` drop (forecast 3→2, anomaly 1→0, since the call never landed)
  and sets `outcome="degraded"`, which exists precisely because **no 500 means no
  other signal**. Two details worth keeping: the fallback is passed as a
  *callable* so the success path never builds a sentence it won't use, and the
  fallback re-states what the prompt used to add conditionally (cold-start
  disclosure, the synthetic-corpus notice) — omit those and the response is only
  honest while the model is up. The agent branch is untouched: there the LLM *is*
  the branch, so an honest 500 is right.
- **A 500 must not carry the exception string, and all five AI routers did.**
  Upstream messages leak ES index names, query DSL, and internal hosts; the
  backend already had `server.error.include-stacktrace: never` and only the AI
  server was out of step — **a setting one side declares and its neighbour
  doesn't is an omission, not a decision** (same shape as `REDIS_PASSWORD`).
  Fixing one and stopping would have repeated the "lesson not carried to the
  neighbouring check" pattern, so all five changed at once, each logging via
  `logger.exception` (drop that and you lose the diagnosis with the leak).
  `tests/test_error_detail_leak.py` scans the sources, because a per-router rule
  is silent when forgotten — and it checks its own regex against a deliberately
  bad string, since a check only ever seen passing is indistinguishable from one
  that always passes.

- **The synthetic corpus and PostgreSQL share overlapping id ranges for
  different entities.** Corpus users are 1–206 and trades 1–26,702; Postgres has
  users 1–5 and trades 1–N. `trade_id=3` is valid in both and means different
  things, so a range check cannot tell them apart. Items are the exception:
  seeding aligned them. Since ADR-0022 an external trade reference **carries its
  own space** — `"syn:3"` / `"pg:3"`, parsed by `parse_ref()` in
  `app/core/ids.py`. A bare `"3"` is a **400**, never a guess. Conversion happens
  at the API boundary only; internally ids stay ints, because they are
  `index + 1` **array positions** that `features.py` uses solely as grouping keys
  — the model has never seen an id as a value.

  Three things about that guard are easy to get wrong:

  - **A well-formed reference to an unwired space is 501, not 400.** `parse_ref()`
    deliberately does not check support; `require_supported()` does that after.
    The request isn't wrong, the server just can't read that plane yet.
  - **`/api/anomaly/alerts` does not pass the guard at all**, and older docs
    claiming "every entry point does" were wrong. It takes no external id and
    walks only the synthetic corpus, so `_summarize()` hardcodes
    `id_space: "synthetic"`. That is safe *today* and false the moment backend
    trades enter that list. No token guard was added there — a check that always
    passes is decoration that buys false confidence.
  - **Parsing `pg:3` is not the same as handling it.** Adding `BACKEND` to
    `SUPPORTED_SPACES` opens every guard at once, which is exactly why it must
    wait for code that turns a Postgres trade into the 11 feature axes;
    history-based features like `pair_trades_7d` are meaningless on 5 demo rows.
    Opening it early replaces an honest 501 with a wrong answer.

`ai/scripts/export_demo_sql.py` generates
`backend/src/main/resources/db/seed-demo.sql` from the same corpus that seeds
Elasticsearch. **Run it (and apply the SQL) whenever corpus items change**, or
Postgres and ES drift apart and search results stop resolving to real rows.

### Gotchas worth knowing before touching this repo
- **`OPENAI_API_KEY` and `ANTHROPIC_API_KEY` belong in `ai/.env`**, not the
  repo-root `.env`. The root one is for docker-compose; the FastAPI app reads
  `ai/.env`, and compose's `ai`/`ai-init` services load that same file via
  `env_file`, so **that one location covers local runs and the container**.
  Both are gitignored. This bit for real: `ANTHROPIC_API_KEY` sat in the root
  `.env` where nothing read it, and the fallback silently did not exist
  (ADR-0042). Related: I claimed the key was absent **without looking** —
  "it isn't there" and "I didn't check" are different statements.
- **A code default that coincidentally equals the `.env.example` value hides a
  missing declaration until the secret is rotated.** The `ai` service got
  `REDIS_URL` but not `REDIS_PASSWORD`, so it connected as `gimp_local_pw` — the
  code default *and* the example value. Locally it worked; the public deploy
  generates its own password, so auth broke there and only there. Two rules fall
  out: **a URL/host and its credential are one pair — shipping one without the
  other is the signal**, and **a setting that one service declares and its
  neighbour doesn't is an omission, not a decision** (the backend had had
  `REDIS_PASSWORD` all along). Same family as the `RABBITMQ_HOST` miss, but the
  trigger is *rotating a secret*, not containerizing — so a containerization
  round does not catch it.
- **What made it survive was the silence, not the typo.** Both consumers of that
  Redis client swallow: the semantic cache's `lookup`/`store` had bare
  `except: pass`, and the AI rate limiter fails open. So the only symptom was a
  0% hit rate — **indistinguishable from the cache simply not matching**, which
  this repo had already documented as expected (threshold 0.98). *When a
  component has a documented reason to look ineffective, a real failure of it
  will hide behind that reason.* Both sites now log a warning; if you add a
  fail-open path, log it — "open but recorded", the rule `core/rate_limit.py`
  already stated. The costlier half was invisible too: with the limiter open,
  `/api/assistant` had no per-minute or daily cap on a public URL.
- **Put every value the verdict used into the failure message.** That is what
  exposed the check added for the bug above as half-vacuous — it printed
  `hit=False, llm_calls=0`, and those two cannot both be right. A pass/fail line
  alone would have shipped it. Corollary: **probe a cache with an intent whose
  uncached cost is nonzero** (`faq_smalltalk` answers without an LLM, so
  `llm_calls == 0` proves nothing there).
- **`settings.openai_temperature` is 0 and `chat()` always sends it explicitly.**
  Omitting the parameter is not "use a sane default" — OpenAI's default is **1.0**,
  and this project ran that way for six phases while treating the resulting
  variance as inherent LLM behaviour (ADR-0017). The one deliberate exception is
  `scripts/generate_hard_negatives.py`, which builds its own client at 1.0 because
  its whole job is producing *varied* user phrasings; don't "unify" that back to 0.
  General lesson: when an LLM behaves non-deterministically here, check the
  request parameters before concluding anything about the model.
- **Prompt edits must be measured, not eyeballed.** A one-line clarification
  telling the model not to confuse `불속성` with `무속성` dropped correct
  extraction from **97.5% to 22%** — naming the confusable value primed the model
  toward it. It was reverted. The line looked obviously correct and would have
  passed review. Three harnesses exist for this:
  `scripts/evaluate_rewrite_determinism.py` (extraction),
  `scripts/evaluate_explanation_prompts.py` (the explanation prompts, ADR-0038)
  and `scripts/evaluate_domain_gate.py` (the domain gate, ADR-0039). The full
  five-case catalogue with a diagnosis order and a checklist is in
  `docs/05-Troubleshooting/LLM-동작을-측정-없이-단정하기.md`.
  - **A before/after comparison needs a same-prompt control.** At
    `temperature=0` one query in ten still gets rewritten differently, so a
    new-vs-old agreement of 0.89 says nothing until you know what old-vs-old
    scores. Running the old prompt twice is what turned "the wording is off" into
    "the schema growing is the cost" — the loss did not move when the wording was
    fixed (−0.234 → −0.248).
  - **Change one thing per run.** Four wording changes went in together and made
    the gate worse (4.13% → 5.30%); which two did it was inferred from reading the
    flagged samples, not measured.
  - **An enumeration meant to widen inclusion gets used as grounds for
    exclusion**, and a prohibition fires on words rather than meaning — listing
    the catalogue of item types cut `"고대 유물 조각 구합니다"`, and a "playing
    the game is out of scope" rule cut `"장비 강화 할 때 쓰는 아이템"` because it
    contains `강화`. Same failure the Korean regexes had, one layer up.
  - **The priming risk did not reproduce the second time** — explicitly telling
    the model *not* to use field names removed leakage completely (12/33 → 0/33).
    That is only knowable by measuring; the same edit could have gone either way.
  - **A tie between variants can mean your metrics don't see the difference.**
    Two candidates scored 0-0-0-0 on the four planned metrics, and only reading
    the samples showed one of them was answering price questions with
    *"거래를 진행하는 것이 좋습니다"* — unsolicited trading advice, 6/33. A fifth
    metric was added and it was rejected. Metrics measure what you thought of.
  - **Collect and score separately.** The first harness scored inline and threw
    the answers away; when a metric turned out to be wrong, re-checking cost
    another 99 LLM calls. With answers saved, adding that fifth metric and
    re-scoring cost **zero** (`--score-only`).
  - **When different inputs produce the identical number, you are measuring the
    detector, not the inputs.** Three distinct prompts all scored exactly 9 on
    "contradiction" — the regex matched `이상 거래로 판별되지 않았습니다` as
    *asserting* an anomaly.
- **A stage's wall-time under load is not its work.** This cost two wrong
  attributions in a row on the cache-hit path. ADR-0025 decomposed `cache_ms`
  into encode 27% / lookup 73% and declined to touch the embedding — but those
  were **10-VU wall-times**. Isolated, `lookup()` is **1.05ms** and
  `encode_one` is **15.77ms**, and `encode_one` is a *synchronous* CPU call
  inside an `async` handler, so it **blocks the whole event loop** (a 10ms
  ticker slipped by 17.28ms on average). The 73% was other requests waiting for
  the 27%. Deferring the embedding on exact hits (ADR-0026) took p95 from
  **279ms to 25.9ms** and throughput from 48 to 316 req/s.
  - **The rest was moved off the loop in ADR-0028**, via a dedicated
    `ThreadPoolExecutor(max_workers=2)` behind `run_cpu()` in
    `app/core/threadpool.py` — **not** `asyncio.to_thread`, whose default
    executor (8 on the target box, 16 here) measured *worse*: torch is 85%
    slower at 4 workers because it already runs intra-op threads. Wrap new
    synchronous CPU calls in `run_cpu`, but **only above an isolated median of
    5ms** — below that the 0.211ms thread hop costs more than it saves, which
    is why the autoencoder (0.31ms) and the LSTM (0.45ms) stay inline.
  - **Re-measured on the 4-OCPU ARM target (2026-08-07,
    `scripts/benchmark_cpu_stages.py`) and the *mechanism* did not survive.**
    `encode_one` gets **faster** with 4 workers there (277 → 241 → 205ms for
    1/2/4, ranges non-overlapping), the exact opposite of the 12-core box where 4
    was +85%. The oversubscription argument scales with core count, so it does not
    transfer. `cpu_pool_workers` **stays 2 anyway**, for a different reason: the
    gain is ~12ms per search against a 4.45s live-LLM p95 (**0.3%**), while 4
    workers would fill all four cores on a box shared with another live project —
    a cost the idle-box benchmark cannot see. Reranker on ARM is only **1.11×**
    slower (108.95ms vs 97.99ms), which also strengthens ADR-0032's rejection of
    arm64 requantization. **The idle ticker floor is 0.11ms on Linux vs 5.54ms on
    Windows** — ADR-0028's "threshold inside the noise floor" failure was a
    Windows timer artifact and does not exist on the target.
  - **Three things the pre-work overturned**: the reranker is the dominant
    blocker (103–189ms, 4–5× `encode_one`), not the embedding; the autoencoder
    was never a target; and `/api/anomaly/*` never blocked the loop at all
    because its handlers are `def`, not `async def` — FastAPI already offloads
    those. The same `detect_trade` **did** block via `/api/assistant`, where
    `build_timeline()` froze the loop for 2.3–3.4s on first call.
  - What this bought is **loop responsiveness only** — ticker p99 29.33 →
    10.65ms, 20ms-overruns 49 → 0~1. Throughput did not move and was never
    predicted to: `cache-warm` went 436.56 → 436.93 req/s. Moving work to a
    thread does not make it faster.
  - When a stage looks far larger than an isolated measurement would predict,
    suspect this first. Checking is cheap: run a 10ms `asyncio.sleep` ticker
    alongside and see how late it wakes.
  - `cache_encode` / `cache_lookup` exist as stages; on a hit `cache_encode`
    must be **absent**, which is the regression signal.
- **Bump `cache_version` when the response *shape* changes, not just when the
  data goes stale**, and — the half that bit next — **whenever the same query
  starts returning a different answer at all.** A cached entry is frozen at the schema it was written with,
  so adding or widening a field means hits keep replaying the old shape — the code
  looks fixed and the screen does not. **It has now bitten twice**, and the
  second time the shape never changed: ADR-0040 added an element filter, so
  `"무속성 검 찾아줘"` returned a *smaller set* — and cached hits kept
  replaying the pre-fix results, which read exactly like "the deploy didn't
  take". The ADR had explicitly reasoned "shape is unchanged, no bump
  needed", reading only half of this rule. **Diagnostic**: the UI's pipeline
  panel shows `cache hit` + `LLM calls 0` — check those badges before
  suspecting the code or the deploy. This bit once before: `resolved_item` grew from
  `{item_id, name}` to the full search item, and cache hits kept rendering a card
  with a name and no price. The setting's comment used to say "reindex / retrain"
  only, which is the *content* case.
- **A script that deliberately corrupts a file to prove a guard fires must
  restore in `finally`, and you must then verify the restore.** Injecting a
  collision is the only way to show an import-time guard actually works, so this
  repo keeps doing it (corpus disjointness, `HARD_FILTER_FIELDS`, id spaces) —
  which means the pattern's failure mode keeps recurring too. It has already bitten
  once: the script died mid-run on an unrelated decode error, never reached its
  restore line, and left the corpus one `element` short. Put such scripts in the
  scratchpad, never in `ai/scripts/`, and end by asserting the target file is
  byte-identical to the original. Corollary: **any exception between mutate and
  restore is a data-loss bug**, so keep that span as small as possible.
- **`ai/requirements.txt` must stay ASCII-only** — pip reads it with the system
  locale codec and a Korean comment breaks the install on cp949. This is one of
  several cp949-vs-UTF-8 boundaries on this Windows setup: Korean in a shell
  argument (`curl -d`) or a Git Bash heredoc gets re-encoded and corrupted,
  Python's Korean `print()` output is mangled by the console codepage (data is
  fine — only the display), and **a child process's Korean stderr must be read as
  bytes and decoded `cp949`** — `subprocess.run(..., text=True,
  encoding="utf-8")` raises on it (this is what triggered the corruption above).
  Write Korean payloads to UTF-8 files from outside the shell; see
  `docs/05-Troubleshooting/한글-인코딩-windows-로케일-코덱.md`.
- **`langchain-community` is pinned `<0.4` on purpose.** ragas 0.4.x
  unconditionally imports `langchain_community.chat_models.vertexai`, which
  0.4.x removed; without the pin `import ragas` fails outright.
- **"The port answers" is not "the process I just started is answering."**
  Stopping `./gradlew bootRun` kills the wrapper but leaves the forked JVM
  holding 8080, so the *next* `bootRun` dies with "Port 8080 was already in use"
  **while `curl localhost:8080` keeps returning 200.** A readiness check that
  only asks "is it up?" passes, and if the survivor is an older build you are
  now verifying stale code. This has already happened twice here. After starting
  a server, confirm the start actually succeeded (the log line, not the port),
  and kill leftovers by PID — `netstat -ano | grep :8080` then
  `taskkill //F //PID <pid>`, since stopping the wrapper does not do it.
- **`docker … prune` frees space inside the vhdx; it does not shrink the file.**
  Pruning 19.35GB of build cache moved host free space by **1.3GB**; the other
  15.7GB needed `diskpart compact vdisk` on `docker_data.vhdx` (Windows Home has
  no `Optimize-VHD`), with Docker fully stopped and `wsl --shutdown` first.
  General rule: **in layered storage, "I deleted it" is a statement by the top
  layer — measure what was actually freed from the outermost one** (`Get-PSDrive C`).
  Two corollaries measured here: `fstrim` reporting **`0 B trimmed` does not mean
  there is nothing to reclaim** (compaction still recovered 15.7GB), and a
  host-side exhaustion surfaces under a different name one layer up — the guest
  says **`read-only file system`**, containerd dumps goroutines, and **the
  `docker` CLI itself stops responding**, so the tool you would diagnose with is
  the victim. Check the host first. Also: restarting Docker Desktop while its old
  processes survive **does not boot the VM at all** — watch `init.log`'s
  timestamp, not the clock. See
  `docs/05-Troubleshooting/도커-디스크-고갈-vhdx-압축.md`.
- **A long-lived dev venv hides missing dependency declarations.** `mcp` and
  `redis` were installed by hand in Phase 6 and never added to
  `requirements.txt`; six months of work ran fine locally and CI failed on its
  first run (ADR-0021). The general rule: **a passing local test suite is no
  evidence that the declarations are complete** — only a fresh environment is.
  CI covers this for anything imported at module level, but **not** for lazy
  imports (`redis` sits inside a function in
  `app/services/cache/dependencies.py`, so collection passes and the server dies
  at the first request instead). When you add an import, add the declaration in
  the same edit; the current lazy-import inventory (6 sites, 5 packages) is in
  `docs/05-Troubleshooting/의존성-선언-누락-오래된-venv.md`. To check by hand
  without waiting for CI: clone the repo to a scratch dir and run it in a
  `python:3.11-slim` container — Windows can't reproduce these, and Actions logs
  need an admin token to download even on a public repo. Procedure in
  `docs/05-Troubleshooting/ci-로그-접근-불가-컨테이너-재현.md`.
- **`settings.embedding_model` points at `models/embedding-finetuned`, which is
  gitignored.** A fresh clone will NOT have it, and the AI server will fail when
  it first tries to embed. Regenerate before running:
  `python -m scripts.generate_hard_negatives` → `python -m scripts.finetune_embedding`,
  then reindex with `python -m scripts.seed_items --recreate`. To fall back to
  the stock model, set `embedding_model` to
  `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (same 384 dims,
  so the index mapping is unchanged) — but reindex either way, since stored
  vectors are model-specific.
- **`embedding_model` is the runtime model; `embedding_base_model` is what
  fine-tuning starts from. Never use the first as a base.** Until ADR-0029 one
  setting served both roles, so `finetune_embedding` fine-tuned its own output
  (fine locally where the directory exists, a HuggingFace 401 on a fresh
  environment) and — worse — `evaluate_embedding`/`compare_eval_sets` compared
  the tuned model **against itself**, which reports a 0 improvement without
  failing. The Phase 4 numbers are not wrong; they were measured with
  `EMBEDDING_MODEL` overridden to the stock model. What was wrong is that the
  committed state could not reproduce them. General rule: **a setting that says
  what to use but not who uses it can silently serve two roles, and it will only
  be wrong on one side** — and any before/after comparison must assert its two
  operands actually differ. Details in
  `docs/05-Troubleshooting/출력-경로를-입력으로-쓴-설정값.md`.
- **The forecast model is gitignored too** (`models/price-lstm`). Unlike the
  embedding case this one fails loudly and usefully: `/api/forecast` returns
  **503** with the exact command to run (`python -m scripts.train_forecast`,
  ~10s on CPU). Cold Start additionally needs the ES index seeded, since donor
  lookup is a kNN query against it.
- **Phase 5 forecast numbers come from synthetic data.** `trade_history.py`
  generates the series from known patterns, so the model's MAPE alone proves
  nothing. `train_forecast` therefore always prints naive baselines
  (last-value / linear drift) and a signal correlation next to it — judge by
  "did it beat the baseline", never by the raw MAPE. Current run: LSTM 4.70%
  vs best naive 4.87%, correlation 0.304.
- **Never set the anomaly threshold from training-set reconstruction error.**
  An autoencoder reconstructs its own training data best, so that percentile is
  optimistically biased — measured here, the train-set p99 fires on 1.32% of
  held-out normal traffic instead of the intended 1.00%. `dataset.py` splits
  normal data three ways specifically so the threshold comes from data the
  model never trained on.
- **The autoencoder and the `max |z|` rule answer different questions.**
  Reconstruction error measures "unrepresentable", not "rare" — a brand-new
  buyer account is reconstructed almost perfectly (the training tail contains
  new accounts), so the AE misses mule-account trades that the rule catches
  100%. The AE wins where evidence is spread across axes. Treat them as
  complementary; don't replace the rule with the model.
- **Semantic cache similarity does NOT separate same-question from
  different-question here.** Measured: trap pairs (`+8 롱소드 시세` vs
  `+9 롱소드 시세`, `100렙 이상` vs `100렙 이하`) reach 0.9787 cosine while
  synonym pairs average 0.845. The base embedding is *worse* than the
  fine-tuned one, so this is a property of sentence embeddings on short
  queries, not a fine-tuning artifact. Hence: **exact-match by default,
  similarity matching only for `faq_smalltalk`**, gated on the stored entry's
  intent. Don't widen that gate without re-running
  `scripts/evaluate_semantic_cache.py`.
  - **"Exact" means after `normalize_query()`** (ADR-0037): trailing sentence
    punctuation and repeated whitespace are stripped before hashing, because
    `"…알려줘!"` missing `"…알려줘"` is a miss nobody wants. **This is not the
    similarity gate being widened** — it is still exact matching, with "same
    text" widened by marks that cannot flip an answer. Keep it that narrow: once
    you start on particles and spacing, `"100렙 이상"`/`"100렙이상"` works but
    there is no principled stop. The guard is
    `test_trap_pairs_still_get_different_keys`, and it is not vacuous — a greedy
    normalizer (strip digits/spaces) collides **4 of the 16** trap pairs while
    the current one collides 0.
- **The intent classifier's confidence is not calibrated.** Trained to loss
  0.013 it gave wrong answers a median confidence of 0.978 vs 0.991 for right
  ones — no threshold separates them, and boundary-query escalation collapsed
  to 0/20. Fixed by *teaching* ambiguity (LLM-generated vague utterances
  labelled `compound`) plus label smoothing, not by tuning the threshold.
  Treat `intent_confidence_threshold` as provisional.
- **`understand_query()` is much steadier since ADR-0017 but not fully
  deterministic.** It used to be rewritten differently run to run, shifting
  BM25/kNN order and reranker scores (measured: 1.31 points on one item). With
  `temperature=0` token-set mode agreement is 0.990 and 1 query in 10 still
  varies, so measurements that depend on rerank scores should still repeat runs
  rather than trust a single one. Separately, **filter extraction is ~1% wrong
  on `"불속성"` (it comes back as `무속성`)** — that is an accuracy bug, not a
  determinism one, and `"무속성 검"` is worse still (see below).
- **The `무속성` direction of the element filter is fixed, and not by touching the
  prompt** (ADR-0040). `"무속성 검 찾아줘"` used to yield `element="무속성"` only 8
  times in 50 — the model reads "무속성" as "no element mentioned" — so a request
  for non-elemental swords still returned 불꽃의 대검, and that matters because
  **35 of the 42 corpus items are 무속성**: losing the filter makes the search
  effectively unfiltered. The prompt already said `무속성/속성 없는 -> "무속성"`
  *and* spelled out the `null`-vs-`무속성` distinction, so it was **already
  instructed in exactly the right words and still wrong 42/50**. Now
  `fill_missing_element()` in `query_understanding.py` fills it from the literal
  query text when extraction returned `null`. Five things to keep:
  - **It only fills, never overwrites**, so it cannot reach the other elements
    (97.5%) and does not touch the separate `불속성 → 무속성` mis-extraction —
    that one is a *wrong value*, not `null`.
  - **It covers `무속성` only. Do not widen it to other elements.** `"화염 저항
    방어구"` contains `화염` but the answer is `null` (element is what an item
    *emits*; resistance is a different axis), and the prompt gets that right
    today — 12/12 on the resistance group. A keyword fill would break it.
    `무속성` is safe precisely because it has no resistance counterpart.
  - **Negation is a known limit, deliberately.** `"무속성 아닌 검"` cannot be
    expressed (`element` is a single equality), so it fills nothing — filtering
    to the exact opposite of the request is far worse than not filtering.
  - **The bigger win was determinism, not accuracy.** The raw extraction swung
    62% ↔ 86% between two runs while the post-checked result was **93% both
    times with the same residual**. A deterministic stage covering a stochastic
    one removes the variance this repo has fought since ADR-0017.
  - **Two pre-registered bars failed and were left failed** (same as ADR-0028).
    Both were mis-written by me: the 타속성 bar was meant to be *relative* to the
    baseline but written as an absolute 95% (before == after, so the change is
    innocent), and the 무속성 95% bar was **unsatisfiable by construction**
    because the eval set deliberately contains one unhandled phrasing
    (`"속성 안 붙은 신발"`) = 7.1% of that group. Adoption rests on the three
    pre-registered *오채움 = 0* bars, on 16/16 changed judgments being correct
    with zero regressions, and on two runs agreeing.
  - Still open, found by the same eval set: **`"어둠 속성 로브"` returns `null`
    6/6** despite the prompt mapping `어둠 -> 암흑`. Same family; not fixed the
    same way, because filling `암흑` by keyword would break `"암흑 저항 목걸이"`.
- **MCP runs over an in-memory transport, not HTTP.** The agent lives in the
  same FastAPI app, and a self-HTTP call would risk deadlock against the known
  event-loop blocking. The protocol is real; only the transport is in-process.
  `python -m app.services.mcp` exposes the same server over stdio.
- **MCP tool output field names are prompt engineering.** The agent once
  compared `price` (listing) against `anchor_price` (last trade) and concluded
  a sword was underpriced. Renaming to `listing_price` / `baseline_price` +
  `baseline_source` fixed it. Keep new tool fields self-describing.

When further implementation lands, keep this section current rather than
letting it drift from what's actually in the repo.

## What this project is

An AI-driven, multi-tenant marketplace for trading game items, accounts, and
in-game currency across multiple game companies ("tenants"). The explicit design
goal is to avoid being "a service that just wraps an LLM API" — instead, requests
are routed through a **request-type-specific AI pipeline** (search AI, prediction
AI, anomaly-detection AI, agent, LLM) so that latency and cost are controlled
per request type rather than funneling everything through a single LLM call.

The author's background is ~2 years as a game GM (domain expertise the design
leans on), and the project is explicitly built as a portfolio piece — several
design decisions below are made partly to demonstrate specific engineering
tradeoffs, not just for technical optimality. Keep that framing in mind: prefer
the industry-standard/explainable choice over the theoretically-fancier one when
the plan indicates a deliberate portfolio tradeoff (e.g. PostgreSQL over Oracle,
Autoencoder over Isolation Forest, ES index-per-tenant over full physical
isolation).

## Planned architecture

### Services
- **Frontend**: React SPA
- **Backend API**: Spring Boot 4.x (Java 21), REST API, separated from frontend.
  Originally planned as 3.x, but by the time Phase 1 implementation started,
  Spring Initializr had dropped 3.x support entirely (compatibility range
  `>=4.0.0`), so the project moved to 4.x (Spring Framework 7 / Jakarta EE 11).
  Note: `redisson-spring-boot-starter` is NOT Boot-4-compatible yet (its
  auto-configuration references a relocated `RedisProperties` class and fails
  context startup) — use the plain `org.redisson:redisson` artifact with a
  manually defined `RedissonClient` `@Bean` instead.
- **AI server**: FastAPI (Python), strict async/await
- **Relational DB**: PostgreSQL (chosen over Oracle for license/ops simplicity and
  to diversify DB experience vs. a prior Oracle 23ai project; not used as the
  primary search/vector store)
- **Search**: Elasticsearch, dedicated 3+ node cluster
- **Cache/locking**: Redis (distributed locks, atomic stock/bid decrement,
  caching, semantic cache)
- **MQ**: RabbitMQ
- **Containerization**: Docker / docker-compose

### Multi-tenancy
Tenant (game company) isolation in Elasticsearch is done via **index-per-tenant**,
not physical cluster isolation — each tenant index can have independent
shard/replica configuration. This is a deliberate tradeoff (documented in the
plan) favoring the industry-standard stack over full isolation.

### AI pipeline: request-type routing
All requests first pass through a lightweight **Intent Router** (rule-based
regex/keyword matching first; ambiguous requests fall back to a fine-tuned
KoELECTRA classifier). Every branch is checked against a **Semantic Cache**
(Redis + embedding similarity) before doing any real work, to skip LLM calls
when possible.

Branches:
- **Simple FAQ / small talk** → immediate response, no LLM call
- **Search query** → Query Rewrite + Text-to-DSL (single LLM call handles both
  synonym expansion and structured filter/JSON extraction) → Elasticsearch
  hybrid search (BM25 + kNN dense_vector, fused via RRF — computed in the
  FastAPI layer, not ES-native: ES's RRF is Platinum-licensed and this
  cluster is basic, see `docs/01-Decisions/0005`) → Cross-Encoder
  re-ranking (ONNX + int8 quantized, top-15~20, batched) → LLM generates
  the natural-language explanation.
  Re-ranker is `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` — the
  *multilingual* mMARCO variant, not the English `ms-marco-MiniLM-L-6-v2`
  originally planned: the English tokenizer shatters Korean into jamo and
  made re-ranking actively worse than no re-ranking at all
  (`docs/01-Decisions/0006`). Korean text analysis in ES uses the **nori**
  plugin, baked into a custom image at `docker/elasticsearch/Dockerfile`.
- **Price inquiry** → if enough trade volume, LSTM/Transformer time-series
  forecast (trained from scratch); if not, Cold Start backoff (find similar
  items via Elasticsearch, inherit their trend weighting) → LLM explanation
- **Anomalous trade check** → Autoencoder reconstruction error → per-feature
  contribution breakdown (for explainability) → LLM explanation
- **Complex/compound query** → Agentic tool-calling, with conditional
  Reflection when confidence is low → LLM final explanation

### Agentic tool-calling via internal MCP servers
The tools used by the "complex/compound query" branch (price prediction,
search, anomaly-detection) are each exposed as **internal MCP servers**, and
the agent calls them through MCP rather than as ad hoc in-process functions.
This is a deliberate scope-limited adoption of MCP: it wraps existing internal
capabilities already being built for this project, with no new external
service integrations (no Steam API / Discord / wiki connectors — see excluded
scope below, that heavier version is still cut). The goal is to demonstrate
MCP server design/implementation without taking on third-party API
auth/parsing/error-handling overhead.

### AI/ML components of note
- Embedding model is fine-tuned in-house: sentence-transformers +
  `MultipleNegativesRankingLoss` + **hard negative mining** (e.g. distinguishing
  "+8" vs "+9" item enhancement levels). Note the plan said hard-negative pairs
  would be LLM-generated; in practice it is a **rules + LLM hybrid** — pairs that
  structured fields can derive (enhancement-level siblings, same-category text
  overlap) are mined deterministically, and the LLM only supplies what rules
  cannot: realistic user-phrased queries and semantic near-misses (element/class
  swaps). Keep that split when extending it.
- Cross-encoder re-ranker must be ONNX-converted and int8-quantized (latency
  budget constraint), capped to top-15~20 candidates, run in batches. Build
  the artifact ahead of time with `python -m scripts.build_reranker_onnx`
  (it is lazily built on first request otherwise, which makes that request
  pay the whole conversion cost).
- CLIP (zero-shot) + OCR is used for screenshot forgery verification, in place
  of a custom-trained CNN forgery detector (excluded — see below).
- Autoencoder is used for transaction anomaly detection instead of Isolation
  Forest, specifically for reconstruction-error-based explainability.
- RAGAS is used to quantitatively evaluate RAG quality (before/after
  fine-tuning, before/after re-ranking) — treat this as the standard for
  judging retrieval/generation quality changes, not ad hoc eyeballing.
  In practice RAGAS is always paired with deterministic IR metrics
  (Recall@k / MRR): the LLM judge varies run to run, so a single RAGAS delta
  on a small eval set is not evidence on its own. The established methodology
  — held-out corpus, dual metrics, embedding-collapse check, query-style
  cross-validation — is in `docs/01-Decisions/0007-임베딩-평가-방법론.md`;
  follow it for future before/after claims.
- LLM provider: OpenAI API is the main provider, used across the whole
  pipeline (Query Rewrite, explanation generation, Agentic Tool-Calling,
  etc). Claude API is a secondary provider, added only for two specific
  stretch-phase uses — not a general second option: (1) the real fallback
  target behind the Resilience4j circuit breaker (so an OpenAI outage
  actually fails over to another LLM instead of just returning a static
  message), and (2) the comparison model in the LLM-Judge evaluation
  harness. Gemini/DeepSeek are intentionally not used — see
  `docs/01-Decisions/0004-llm-provider-openai-claude.md` for the reasoning
  (a prior project used 4 providers; this one only adds a provider when
  there's a concrete use for it).

### Explicitly excluded from scope
Do not introduce these unless the user asks — they were deliberately cut to
avoid scope creep: external MCP connectors (Steam API, Discord, game
patch-notes/wiki integrations), Knowledge Graph/Graph RAG, CNN-based forgery
detection, Isolation Forest, collaborative-filtering recommendations, LoRA
fine-tuning. Note: internal-tool MCP servers (see above) are in scope — only
the external-service-integration version of MCP is excluded.

### Traffic handling (high-contention item purchase/bid flow)
1. Acquire a Redis distributed lock on the item
2. On success, publish a trade-processing message to RabbitMQ
3. A consumer processes messages in order within a DB transaction
4. Post-trade notifications and price updates happen asynchronously

Load testing (k6 / nGrinder) is expected to produce real throughput/latency
numbers rather than relying on estimates.

### Infra/deployment constraints
Target deployment is Oracle Cloud ARM, 4 OCPU / 24GB, shared with a prior
project — so services are brought up **on-demand**, not run continuously.
Elasticsearch (JVM-heavy) needs an explicit heap size cap and its 3-node cluster
is only started for demos. ML models use lazy loading rather than staying
resident. Keep this constraint in mind when making decisions about default
resource usage, startup behavior, and always-on services/daemons.

### Observability / resilience / security (stretch additions per the plan)
- Observability: Prometheus + Grafana, OpenTelemetry, ELK
- Resilience: Resilience4j circuit breakers, idempotency keys
- Security: per-tenant JWT claims, API Gateway, rate limiting
- CI/CD: GitHub Actions — build → test → RAGAS quality gate → Docker image push

## Documentation workflow (Obsidian vault under docs/)

This project keeps an Obsidian vault at `docs/` alongside the code, with these
folders: `00-Architecture`, `01-Decisions` (ADRs), `02-AI-Pipeline`,
`03-API-Specs`, `04-DevLog`, `05-Troubleshooting`, `06-발표`. Keep it up to date
as work happens, without being asked each time:

- **Architecture/technology decision finalized** (e.g. choosing a library,
  picking an algorithm, changing a prior choice): propose creating or updating
  an ADR in `docs/01-Decisions/ADR-{next-number}-{short-title}.md` using the
  format: 상태 / 배경 / 결정 / 고려한 대안 / 영향. Ask before writing if it's
  not obvious the decision is truly final.
- **A meaningful chunk of work is completed** (a feature, an endpoint, a
  pipeline stage): after finishing, propose appending a short entry to
  `docs/04-DevLog/{YYYY-MM-DD}.md` summarizing what was done — a few bullet
  points, not a full transcript of the session.
- **A bug or infra issue was diagnosed and fixed**: propose writing an entry to
  `docs/05-Troubleshooting/{short-title}.md` with 문제 / 발생 원인 / 해결 방법 /
  배운 점.
- Always propose the doc update rather than silently writing it, unless the
  user has already said to just go ahead. Keep entries concise — this is a
  running log, not a report.
- **`02-AI-Pipeline/요청-타입별-파이프라인.md` and `03-API-Specs/API-명세.md`
  now exist** (written 2026-07-31, once the search work settled). They
  consolidate ADR-0005…0018 into one flow and one endpoint reference. Treat them
  as **derived documents**: when a pipeline stage or an endpoint changes, the ADR
  records *why* and these two record *what is true now* — update them in the same
  pass, don't let them drift. Both were written from live responses and the
  actual router/DTO definitions, not from memory; keep that standard.
