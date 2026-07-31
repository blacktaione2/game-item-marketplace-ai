# 게임 아이템 통합 거래 플랫폼

여러 게임사(테넌트)의 아이템·계정·게임머니를 하나의 플랫폼에서 거래하는
멀티테넌트 거래소. **요청 유형별로 다른 AI 파이프라인을 태워** 지연시간과
비용을 유형 단위로 통제한다.

`Spring Boot 4` · `Java 21` · `FastAPI` · `Python 3.11` · `React` ·
`PostgreSQL` · `Elasticsearch` · `Redis` · `RabbitMQ` · `Docker`

> ## ⚠️ 인증이 없습니다
>
> 데모 목적이라 인증을 구현하지 않았습니다. 서버는 `X-Tenant-Id` /
> `X-User-Id` 헤더 값을 **그대로 신뢰**하고, 프론트엔드는 데모 사용자
> 드롭다운으로 그 값을 바꿉니다. **이 상태의 서버를 외부에 노출하지
> 마세요.** JWT 도입은 Phase 8 진행 항목입니다.
>
> 저장소 공개와 서버 노출은 다른 문제입니다 — 코드에 비밀정보는 없습니다.

---

## 설계 목표

**"LLM API를 감싸기만 한 서비스"가 되지 않는 것.**

모든 요청을 하나의 LLM 호출로 흘려보내면 간단하지만, 그러면 FAQ 한 줄에도
검색 한 번에도 같은 비용과 지연이 붙는다. 이 프로젝트는 요청이 무엇인지 먼저
판별하고 종류마다 다른 경로를 태운다.

```mermaid
flowchart TD
    Q[사용자 질의] --> C{시맨틱 캐시}
    C -->|적중| R[응답 · LLM 0회]
    C -->|미적중| RT[Intent Router<br/>규칙 → KoELECTRA]
    RT --> F[FAQ · 정적 응답<br/>LLM 0회]
    RT --> S[검색<br/>BM25+kNN → RRF → 리랭커<br/>LLM 1~2회]
    RT --> P[시세 예측<br/>LSTM / Cold Start<br/>LLM 2회]
    RT --> A[이상거래 탐지<br/>오토인코더<br/>LLM 1회]
    RT --> AG[에이전트<br/>MCP 도구 호출<br/>LLM 1~6회]
```

| 요청 유형 | 파이프라인 | LLM 호출 |
|---|---|---|
| FAQ·스몰토크 | 정적 응답 | **0회** |
| 아이템 검색 | Rewrite+Text-to-DSL → BM25+kNN → RRF → 크로스인코더 재순위 | 1~2회 |
| 시세 문의 | 거래 이력 충분 시 LSTM, 부족하면 Cold Start 백오프 | 2회 |
| 이상거래 점검 | 오토인코더 재구성 오차 + 피처별 기여도 분해 | 1회 |
| 복합 질의 | MCP 도구를 부르는 순차 에이전트 | 1~6회 |

상세: [요청 타입별 파이프라인](docs/02-AI-Pipeline/요청-타입별-파이프라인.md)

### 아키텍처

```mermaid
flowchart LR
    FE[React SPA<br/>:5173] -->|Vite dev proxy| BE[Spring Boot<br/>:8080]
    FE -->|Vite dev proxy| AI[FastAPI<br/>:8000]
    BE --> PG[(PostgreSQL)]
    BE --> RD[(Redis<br/>분산 락)]
    BE -.헬스체크.-> AI
    AI --> ES[(Elasticsearch<br/>nori)]
    AI --> RD
    AI --> LLM[OpenAI API]
```

Vite dev proxy가 브라우저에 단일 오리진만 보여주므로 **양쪽 서버 모두 CORS
설정이 없다.**

---

## 측정된 결과

이 프로젝트에서 가장 신경 쓴 것은 기능 수가 아니라 **주장에 근거가 붙어
있는가**다. 그래서 개선한 것과 **기각한 것을 같이 적는다.**

### 개선

| 항목 | 이전 | 이후 |
|---|---|---|
| 임베딩 파인튜닝 — 홀드아웃 Recall@1 | 0.148 | **0.389** |
| 임베딩 파인튜닝 — 홀드아웃 MRR | 0.301 | **0.510** |
| 하드 필터 — 부적합 검색 결과 | 50건 | **14건** (적합 손실 0) |
| 질의 재작성 결정성 — 토큰집합 모드 일치율 | 0.840 | **0.990** |
| 0건 응답 — 같은 입력 5회의 답변 종류 | 5가지 | **1가지** |

### 기각 — 재보고 접은 것

| 항목 | 결론 |
|---|---|
| **리랭커 점수 하한** | **2회 측정, 2회 기각.** 사유가 달랐다 — 처음엔 노이즈가 마진보다 커서, 두 번째는 질의 간 점수 눈금이 비교 불가라서. [ADR-0018](docs/01-Decisions/0018-리랭커-하한-재측정.md) |
| **시맨틱 캐시 유사도 매칭** | 함정 쌍(`+8 롱소드` vs `+9 롱소드`)이 0.9787인데 동의 쌍은 0.845. **함정이 더 유사하다.** FAQ에만 허용 |
| **프롬프트로 혼동 방지** | `불속성`/`무속성`을 구분하라는 한 줄이 정답률을 **97.5% → 22%** 로 떨어뜨렸다. 되돌림 |

### 부하테스트 실측 ([ADR-0020](docs/01-Decisions/0020-부하테스트.md))

k6. **지연시간 목표를 미리 걸지 않고**, 자의적이지 않은 이진 기준만 검증했다.

| | 결과 |
|---|---|
| **오버셀** | **0건** — 동시 구매 26,600여 건에서 `재고 감소분 == 성공 응답 수`. 분산 락 + 낙관적 락의 주장을 실동시성에서 처음 검증 |
| 처리량 상한 (단일 아이템 경합) | **107 req/s**, knee ≈ 4 VU |
| 병목 — 경합 시 | 락 **대기** 0.178s vs **보유** 0.007s → 큐잉 |
| 병목 — 분산 시 | 429 req/s로 4배, 대신 보유가 5배 올라 **DB가 제약** |
| AI 검색 (캐시 적중) | p95 **283ms**, LLM 호출 0회 |
| AI 검색 (실 LLM) | p95 **4.45s** — 그중 **97%가 LLM 2회**. ES+리랭커+임베딩은 82ms |

**병목은 하나가 아니라 부하 형태에 따라 옮겨간다.** 대조군(분산 프로파일)
없이 쟀으면 "락이 병목"에서 끝났을 것이다.

부하 생성기가 측정 대상과 같은 박스(4 OCPU 공유)에 있어 **보수적인 수치**다.

### 기준선 대비 — 이긴 것과 진 것

모든 모델을 단순 기준선과 비교했고 **진 것은 진 대로 적는다.**

| 모델 | 결과 |
|---|---|
| 시세 예측 LSTM | MAPE 4.70% vs 최고 naive 4.87%. **이겼지만 근소하다** — 합성 데이터라 예측 가능한 정보량이 원래 적다 |
| 이상탐지 오토인코더 | 신규 계정 거래는 **규칙(`max\|z\|`)이 100%, AE는 놓친다.** 여러 축에 흩어진 이상은 반대. 상호 보완이지 대체가 아니다 |
| 의도 라우터 | 확신도가 **교정되어 있지 않다**(틀린 답 0.978 vs 맞은 답 0.991). 임계값이 아니라 학습 데이터로 고쳤다 |

---

## 실행 방법

### 사전 요구사항

Docker · JDK 21 · Python 3.11 · Node.js · **OpenAI API 키**(런타임에만 필요)

### 1. 인프라

```bash
cp .env.example .env      # 필요하면 포트·자격증명 조정
docker compose up -d      # PostgreSQL · Elasticsearch · Redis · RabbitMQ
```

Elasticsearch는 이미지를 직접 빌드한다 — 한국어 형태소 분석기(nori)를 구워
넣기 위해서다.

### 2. AI 서버 — 모델을 먼저 만들어야 한다

> **새로 클론하면 검색이 바로 동작하지 않는다.** 파인튜닝된 임베딩과 리랭커
> ONNX 아티팩트는 용량 때문에 저장소에 없다. 아래 재생성 단계를 건너뛰면
> 첫 검색 요청에서 실패한다.
>
> **재생성 자체에는 API 키가 필요 없다.** 학습 트리플과 의도 학습 데이터는
> 재현성을 위해 `ai/data/`에 커밋해뒀다. 키는 런타임(질의 재작성·설명 생성)에
> 필요하다.

```bash
cd ai
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt   # Windows
# source .venv/bin/activate && pip install -r requirements.txt   # Linux/macOS

cp .env.example .env      # OPENAI_API_KEY 를 채운다 (루트 .env가 아니다)

# 모델 재생성 — 전부 로컬 CPU, LLM 호출 없음
python -m scripts.finetune_embedding      # data/train_triplets.jsonl 사용
python -m scripts.build_reranker_onnx     # HF 모델 → ONNX int8
python -m scripts.train_forecast          # ~10초
python -m scripts.train_anomaly
python -m scripts.train_intent_router     # data/intent_train.json 사용

# Elasticsearch 색인
python -m scripts.seed_items --recreate

python -m uvicorn app.main:app --port 8000
```

학습 데이터를 처음부터 다시 만들려면 `generate_hard_negatives`,
`generate_intent_data`, `generate_eval_queries`를 쓴다. **이 셋만 LLM을
호출한다** — 커밋된 데이터가 있으므로 보통은 불필요하다.

파인튜닝을 건너뛰고 싶으면 `ai/.env`에 스톡 모델을 지정한다(차원이 384로
같아 인덱스 매핑은 그대로다). 다만 위 Recall@1 수치는 재현되지 않는다.

```
EMBEDDING_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
```

### 3. 백엔드

```bash
cd backend
./gradlew bootRun          # :8080
```

데모 데이터를 넣으려면 — 검색 결과(ES)를 클릭해 상세(PostgreSQL)로 이동하려면
양쪽 아이템 id가 맞아야 한다.

```bash
cd ai && python -m scripts.export_demo_sql
docker exec -i gimp-postgres psql -U gimp -d gimp < ../backend/src/main/resources/db/seed-demo.sql
```

### 4. 프론트엔드

```bash
cd frontend
npm install
npm run dev                # :5173
```

### 테스트

```bash
cd ai && python -m pytest  # 68건. 외부 서비스·모델 불필요
```

---

## 문서

`docs/`는 Obsidian 볼트다. **결정의 근거와 정정 이력이 여기 있다.**

| 폴더 | 내용 |
|---|---|
| [00-Architecture](docs/00-Architecture/) | 기획서, 개발 로드맵 |
| [01-Decisions](docs/01-Decisions/) | **ADR 18건.** 상태/배경/결정/고려한 대안/영향 |
| [02-AI-Pipeline](docs/02-AI-Pipeline/요청-타입별-파이프라인.md) | 요청 유형별 실행 흐름 종합 |
| [03-API-Specs](docs/03-API-Specs/API-명세.md) | 두 서버의 엔드포인트·상태 코드 |
| [04-DevLog](docs/04-DevLog/) | 날짜별 경과 |
| [05-Troubleshooting](docs/05-Troubleshooting/) | 재발 조건이 실재하는 진단 패턴 9건 |

읽을 것을 하나만 고른다면 [ADR-0018](docs/01-Decisions/0018-리랭커-하한-재측정.md)
— 하나의 결정이 기각되고, 원인이 밝혀져 되살아났다가, 분할을 전수 열거해
다시 기각되는 과정이 수치와 함께 있다.

---

## 알려진 한계

과신을 막기 위해 한자리에 모은다.

| 항목 | 상태 |
|---|---|
| **인증** | **없다.** 헤더를 그대로 신뢰한다 (Phase 8 진행 중) |
| 시세 예측 정확도 | 합성 데이터 기준. 기준선 대비 근소 우위 |
| 이상탐지 재현율 | 합성 주입 기준. 규칙이 이기는 시나리오가 있다 |
| 라우터 확신도 | 교정 안 됨. 임계값은 잠정값 |
| `"무속성"` 필터 | 추출이 50회 중 8회라 **사실상 무력** — 모델이 "속성 언급 없음"으로 읽는다 |
| 등급(`전설`) 축 | 필드가 없어 텍스트 신호로만 동작 |
| 식별자 공간 | 합성 코퍼스와 PostgreSQL의 users·trades id 범위가 겹친다. 가드가 501로 막고 있고 통합은 Phase 8 항목 |
| 백엔드 테스트 | Initializr 기본 `contextLoads()` 하나뿐. CRUD·락·거래는 수동 검증 |

---

## 만든 사람

게임 GM 약 2년 경력을 도메인 전문성으로 연결한 포트폴리오 프로젝트입니다.
설계 판단 중 일부는 기술적 최적해보다 **설명 가능성과 업계 표준**을 택했고,
그 근거는 각 ADR에 적혀 있습니다(예: Isolation Forest 대신 오토인코더 —
재구성 오차를 피처별로 쪼갤 수 있어서).
