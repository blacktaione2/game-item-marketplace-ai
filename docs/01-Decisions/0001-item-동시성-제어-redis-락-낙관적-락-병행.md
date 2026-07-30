---
status: 채택됨
created: 2026-07-28
---

# ADR-0001: 아이템 동시성 제어에 Redis 분산 락과 JPA 낙관적 락(@Version)을 함께 쓴다

## 상태

채택됨

## 배경

구매/입찰(`TradeService.purchase`, `TradeService.bid`)은 같은 `Item` 행을
여러 요청이 동시에 수정하는 전형적인 동시성 문제다(재고 초과 판매, 낮은
입찰가가 최고 입찰가를 덮어쓰는 등). 아키텍처 계획(`CLAUDE.md`의 "고경합
아이템 구매/입찰 흐름")에서 Redis 분산 락으로 아이템 단위 상호배제를
거는 것을 1차 수단으로 정해뒀고, 실제로 `TradeService`는 Redisson
`RLock.tryLock()`으로 `lock:item:{itemId}`를 잡은 뒤에만 트랜잭션을
실행한다.

여기에 더해 `Item` 엔티티에 JPA `@Version` 컬럼을 추가할지가 쟁점이었다.
Redis 락이 정상 동작하는 한 `@Version`은 이론상 아무 일도 하지 않는
군더더기 컬럼이다.

## 결정

Redis 분산 락을 주 동시성 제어 수단으로 유지하되, `Item.version`
(`@Version`)을 낙관적 락으로 병행한다. 즉 "혹시 모를" 상황에 대비한
이중 안전장치다.

Redis 락만으로는 막을 수 없는, 그러나 실제로 일어날 수 있는 시나리오들:

- `LOCK_LEASE_SECONDS`(5초)를 넘겨 처리가 끝나기 전에 락이 만료되고, 그
  사이 다른 요청이 같은 락을 잡아 동시에 같은 행을 수정하는 경우
  (GC 정지, 느린 쿼리, 커넥션 풀 고갈 등으로 충분히 발생 가능)
- Redis 자체 장애/재시작 직후 락 상태가 유실된 상태에서 요청이 몰리는
  경우
- 향후 코드가 늘어나면서 실수로 락을 거치지 않고 `Item`을 수정하는
  경로가 추가되는 경우 (예: 관리자 배치 작업, 다른 서비스의 직접 수정 등)

`@Version`은 이런 경우에도 `UPDATE ... WHERE id=? AND version=?`가
0 rows affected가 되는 순간 `OptimisticLockingFailureException`으로
드러나므로, 데이터가 조용히 깨지는 대신 명시적인 실패(409 Conflict)로
전환된다. (`GlobalExceptionHandler.handleOptimisticLock` 참고.)

## 고려한 대안

1. **Redis 락만 사용, `@Version` 없음** — 가장 단순하지만 위 시나리오들에서
   무방비. 락이 "믿을 수 있다"는 가정이 깨지는 순간 재고 오버셀/입찰
   역전 같은 조용한 데이터 오류로 이어진다.
2. **DB 비관적 락(`SELECT ... FOR UPDATE`)만 사용, Redis 락 없음** — 단일
   DB 트랜잭션 안에서는 확실하지만, 커넥션을 오래 점유하고 향후
   RabbitMQ 컨슈머 등 애플리케이션 레벨에서 재사용할 수 있는 락 개념이
   아니라서 로드맵상 다음 단계(비동기 처리 파이프라인)와 잘 안 맞는다.
3. **Redis 락 + `@Version` 병행 (채택)** — 컬럼 하나, 예외 핸들러 하나
   추가하는 비용으로 이중 안전장치를 얻는다. 정상 경로에서는 거의
   트리거되지 않아야 하는 코드이므로, 실제로 `OptimisticLockingFailureException`이
   발생한다면 그 자체가 "Redis 락이 어딘가에서 새고 있다"는 신호로
   모니터링에 활용할 수 있다.

## 결과

- `Item`에 `@Version private Long version` 필드가 있다
  (`backend/src/main/java/com/gimp/backend/domain/item/Item.java`).
- `GlobalExceptionHandler`가 `OptimisticLockingFailureException`을
  `409 Conflict`로 매핑해서 사용자에게 재시도를 안내한다. (이 매핑이
  빠져 있으면 낙관적 락 충돌이 500으로 새어나가 "이중 안전장치"라는
  주장이 무색해지므로, 이 ADR과 함께 추가했다.)
- 향후 이 예외가 실제로 발생하는 빈도를 관측성 스택(Prometheus/Grafana,
  계획 문서의 스트레치 목표)에 태워서 락 만료 시간(`LOCK_LEASE_SECONDS`)이
  적절한지 판단하는 지표로 쓸 수 있다.
