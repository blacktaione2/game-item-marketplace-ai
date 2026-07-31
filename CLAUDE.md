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

- **`llm_calls` is 1 on this path, not 0.** `understand_query` inside
  `run_search` already ran and **cannot be skipped** — the verdict is defined by
  which filters got extracted. Only the explanation call disappears (2 → 1).
  `llm_calls == 0` means a cache hit and nothing else.
- **A no-result response is never stored in the cache** — `is_cacheable()` takes
  the response and vetoes on `no_results`. The verdict rests on non-deterministic
  filter extraction, so caching it by query string would freeze one arbitrary
  draw for the whole TTL, and "nothing exists" is the answer that goes stale
  fastest. Don't confuse this with the roadmap's *rewrite* caching idea (query →
  rewritten query), which is fine and would help.

Scope limit worth knowing: this handling does **not create** zero-hit results,
it only speaks correctly about ones that already happen. `"2만원 이하 전설 등급
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

**Phase 8 is underway**, in a fixed order with dependencies worked out (see the
roadmap). Done so far: README + GitHub remote (`game-item-marketplace-ai`,
public), **observability stage 1** (ADR-0019), **load testing** (ADR-0020), and
**CI/CD stage 1** (ADR-0021), and **id-space prefixing** (ADR-0022, scoped down
to the representation layer). Next is **security (JWT + rate limiting)**.

CI is three GitHub Actions workflows (`.github/workflows/{ai,backend,frontend}.yml`),
split into separate files so each can carry its own `paths` filter — a
docs-only commit runs nothing. Two things about it are load-bearing:

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
  no ES or AMQP dependency.

Note what the green badges do and don't mean: the backend suite is still the
single Initializr `contextLoads()` and asserts no behaviour. It is worth running
only because context-startup failure is a regression class that actually bit here
(the Redisson/Boot-4 `RedisProperties` relocation).

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
remaining items all have prerequisites: `"무속성"` extraction needs a prompt eval
set, the `rarity` axis needs a grade taxonomy invented, and the reranker floor
needs cross-query score comparability (see above). Anything that would have
followed floor adoption — notably splitting "no results matched your conditions"
from "results matched but scored too low" — **is moot**, since nothing cuts on
relevance now.

A methodological note worth carrying: repeating a procedure on the **same**
tune/holdout split measures noise, not generalisation. Enumerating splits over
the same collected data costs nothing extra (no new API calls) and is what
turned an apparently adoptable threshold into a rejected one. Do that before
adopting any threshold calibrated on a split.

### Infra
`docker-compose.yml` at the repo root brings up PostgreSQL, single-node
Elasticsearch, Redis, and RabbitMQ (see that file / `.env.example` for ports
and credentials). Elasticsearch is **built from `docker/elasticsearch/Dockerfile`**,
not pulled directly — it bakes in the `analysis-nori` Korean plugin, which
would otherwise be lost whenever the container is recreated.

### backend/ (Spring Boot 4.x, Java 21, Gradle)
Package layout: `domain` (entities per aggregate: `tenant`, `user`, `item`,
`trade`, plus `domain.common.BaseTimeEntity`), `repository`, `service`,
`controller`, `dto`, `config`, `client`, `exception`. Every entity table
carries `tenant_id` from the start. Implemented: Item CRUD, Redis-lock-backed
purchase/bid (`ItemController`, `TradeController`), and `GET /api/health`
which proxies a health check to the FastAPI server.

Build/run from `backend/`: `./gradlew build`, `./gradlew bootRun` (port 8080).
No lint task configured yet. **Note the test suite is only the Initializr
default `contextLoads()`** — it needs the live Postgres/Redis from
docker-compose to pass, but it asserts nothing about behaviour. CRUD, the Redis
lock, and the trade flow were verified by hand with curl; see the roadmap's
기술 부채 section.

### ai/ (FastAPI, Python 3.11)
- `app/routers/` — health, llm, search, forecast, anomaly, **assistant**
  (`POST /api/assistant` is the unified entry point; the per-capability
  endpoints stay because the MCP tools wrap them)
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
- `app/services/search/` — mapping, embedding, es_client, filters, hybrid
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
  `intent_utterances.py` holds the **hand-written** router eval + boundary sets
  (LLM-generated training utterances live in `data/intent_train.json`);
  `cache_pairs.py` holds the synonym/trap pairs for cache threshold work.
- `scripts/` — seed_items, build_reranker_onnx, generate_hard_negatives,
  generate_eval_queries, finetune_embedding, evaluate_embedding,
  compare_eval_sets, train_forecast, train_anomaly, generate_intent_data,
  train_intent_router, evaluate_semantic_cache, evaluate_rerank_floor,
  evaluate_hard_filters, evaluate_rewrite_determinism. **The last two answer different questions and must stay
  separate**: `evaluate_rerank_floor` documents a *rejected* approach (it sweeps
  thresholds and would print a recommendation), while `evaluate_hard_filters`
  measures how many unfit results a threshold-free filter leaves. It A/Bs within
  a single run — comparing against a previous run's aggregate lets query-rewrite
  drift contaminate the delta. Its `is_fit()` labels off the item **name**, never
  the filtered field, or measuring the filter becomes circular.
- `data/` — generated triplets and eval query sets (tracked; small and worth
  versioning for reproducibility). `models/` is gitignored — regenerate with
  the build/finetune scripts.

- `tests/` — `python -m pytest` from `ai/` (78 tests, no `pytest-asyncio` — the
  few async cases use `asyncio.run`). Deliberately limited to
  deterministic units: RRF fusion, router rules, cache keys/tenant isolation,
  per-intent TTL + the no-result storage veto, id-space guards, filter→DSL
  conversion, no-result answer construction, temperature plumbing. Model quality is judged
  by the training scripts' held-out
  reports, not by unit tests. Everything else is manually verified (see the
  roadmap's 기술 부채 section).

Setup: `python -m venv .venv && .venv/Scripts/python -m pip install -r
requirements.txt`, then `python -m uvicorn app.main:app --port 8000`.
Seed search data with `python -m scripts.seed_items --recreate` (indexes
train + eval items).

### frontend/ (Vite + React + TypeScript)
`npm install` then `npm run dev` (port 5173). **A Vite dev proxy makes the
browser see one origin** — `/api/backend/*` → 8080, `/api/ai/*` → 8000 — which
is why neither server has CORS configured; don't add it. Screens: `/`
(assistant + search), `/items/:id` (detail + price chart + purchase/bid),
`/anomalies` (GM queue). State is TanStack Query only; there is no global store.

**There is no authentication.** A demo-user dropdown picks the `X-User-Id` sent
to the backend, matching the placeholder headers the controllers expect. Don't
expose this deployment. Note also that the backend wants `X-Tenant-Id: 1`
(Long) while the AI server wants `tenant_code: "nexon"` (str) — `src/demo.ts`
absorbs that difference so neither server had to change.

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
- **`OPENAI_API_KEY` belongs in `ai/.env`**, not the repo-root `.env`. The root
  one is for docker-compose; the FastAPI app reads `ai/.env`. Both are
  gitignored.
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
  passed review. There is a harness for exactly this:
  `scripts/evaluate_rewrite_determinism.py`.
- **The semantic cache pays for an embedding it usually doesn't use.** `ask()`
  encodes the query *before* calling `cache.lookup()`, but `lookup()` tries the
  exact-match key first and that path needs only a hash of the query string.
  Exact match is the default for every intent except FAQ (ADR-0012), so a cache
  hit burns ~108ms of encoding for nothing — measured, and it is most of the
  283ms p95 on the cache-hit path. Registered on the roadmap; making it lazy
  should be verified with `load/run.sh ai cache-warm`, not assumed.
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
- **The `무속성` direction of the element filter does not work.** `"무속성 검
  찾아줘"` yields `element="무속성"` only 8 times in 50; the other 42 come back
  `null`, because the model reads "무속성" as "no element mentioned". So a request
  for non-elemental swords still returns 불꽃의 대검. The data is fine — the
  extraction isn't. Fixing it needs a prompt eval set first (see the prompt-edit
  gotcha above); it is registered on the roadmap.
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
`03-API-Specs`, `04-DevLog`, `05-Troubleshooting`. Keep it up to date as work
happens, without being asked each time:

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
