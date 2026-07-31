# 부하테스트

k6 기반. **처리량 수치보다 두 가지 판정이 목적이다** — 오버셀이 일어나는가,
그리고 병목이 어디인가.

## 사전 준비

```bash
winget install GrafanaLabs.k6          # 또는 https://k6.io/docs/get-started/installation/

docker compose up -d                   # observability 프로파일은 켜지 않는다 (아래 참고)
docker exec -i gimp-postgres psql -U gimp -d gimp \
  < backend/src/main/resources/db/seed-loadtest.sql

cd backend && ./gradlew bootRun        # :8080
cd ai && python -m uvicorn app.main:app --port 8000   # 시나리오 B에만 필요
```

> **`seed-loadtest.sql`이 없으면 락을 못 잰다.** 데모 아이템은 `stock=10`이라
> 10요청 만에 재고가 마르고, 그 뒤로는 전부 `invalid_request` 거절이 된다.
> 그러면 락 대기가 0에 수렴해서 **경합이 아니라 재고 고갈을 측정하게 된다.**
> 실제로 초기 스모크에서 8건 중 2건이 그렇게 거절됐다.

## 실행

```bash
./load/run.sh purchase contended 20 30s   # 한 아이템에 집중
./load/run.sh purchase spread    20 30s   # 20개로 분산 (대조군)
./load/run.sh purchase step               # 계단식 — knee 탐색
./load/run.sh ai cache-warm 10 40s        # 캐시 적중 (llm_calls=0)
./load/run.sh ai live-llm    3 30s        # 실제 LLM 호출
```

`run.sh`가 매 실행마다 스냅샷 → k6 → 스냅샷 → 차분 → **재고 정합성 확인**까지
한다. 결과는 `load/out/`에.

## 판독 규칙 (측정 전에 정해둔 것)

사후에 그럴듯한 해석을 붙이지 않으려고 미리 적어둔다.

| 관측 | 결론 |
|---|---|
| 대기↑, 보유 평평 | 병목은 **큐잉**(락). 처리량 상한 ≈ `1 / 보유시간` |
| 대기↑, 보유도 ↑ | 병목은 **트랜잭션**(DB). 락은 증상이지 원인이 아니다 |
| `spread`에서도 대기 | Redisson 클라이언트 또는 Redis 자체 |
| `invalid_request` > 0 | **시나리오 오염** — 재고가 부족하다. 수치를 믿지 말 것 |
| `optimistic_conflict` > 0 | **분산 락이 새고 있다** |

## 성공 기준 — 지연시간 목표를 걸지 않는다

이 프로젝트는 임계값을 감으로 정했다 뒤집힌 전례가 둘 있다(오토인코더 학습셋
편향, 시맨틱 캐시 유사도). 첫 측정에는 비교 기준이 없으므로 **곡선을 먼저
그리고 SLO는 그 뒤에 정한다.**

대신 **자의적이지 않은 이진 기준**만 건다.

- **오버셀 0건** — `재고 감소분 == 201 응답 수 + 워밍업 1건`
- **5xx 0건** (409는 정상 거절이다)
- **`optimistic_conflict` 0건**

## 워밍업

`setup()`에서 대상 엔드포인트를 1회 호출해 지연 로딩을 흡수한다. AI 쪽은
임베딩·리랭커·KoELECTRA가 전부 `lru_cache` + 메서드 내부 로딩이라 **첫 요청이
수십 초** 걸린다(실측 20~35초, 대부분 임베딩 모델 로딩).

**이 값을 버리지 않는다.** `[warmup] cold_start_ms=...` 로 찍어 콜드 스타트
비용으로 보고한다 — 온디맨드 기동 설계에 직접 영향을 준다.

## 한계 — 수치를 읽을 때 감안할 것

- **부하 생성기가 측정 대상과 같은 박스에 있다.** 4 OCPU를 다른 프로젝트와
  공유하는 환경이라 전용 생성기라면 더 나온다. **이 수치는 보수적이다**
- **Prometheus/Grafana를 끈 채로 돌린다.** 스크래핑이 측정 대상과 CPU를
  경합한다(ADR-0019). 집계는 `/metrics` 전후 스냅샷 차분으로 얻는다 —
  히스토그램은 누적값이라 차분이 곧 구간 집계다
- **시나리오 A와 B를 동시에 돌리지 않는다.** ES(512MB 힙)와 백엔드를 같이
  때리면 둘 다 판독 불가가 된다
- `live-llm`은 동시성을 낮게 간다. 올리면 OpenAI rate limit에 걸려 **우리
  시스템이 아니라 429 재시도를 측정**하게 된다

측정 결과와 해석은 `docs/01-Decisions/0020-부하테스트.md`.
