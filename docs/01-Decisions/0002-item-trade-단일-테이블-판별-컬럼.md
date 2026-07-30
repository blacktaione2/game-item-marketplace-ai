---
status: 채택됨
created: 2026-07-28
---

# ADR-0002: Item(정가/경매), Trade(구매/입찰)는 서브타입 분리 없이 단일 테이블 + 판별 컬럼으로 설계한다

## 상태

채택됨

## 배경

아이템은 정가 판매(`FIXED_PRICE`)와 경매(`AUCTION`) 두 가지 판매 방식을
가진다. 경매 아이템만 `currentBidPrice`/`currentBidder`가 의미를 갖는다.
거래도 즉시 체결되는 구매(`PURCHASE`)와, 낙찰 전까지 상태가 바뀌는
입찰(`BID`)로 나뉜다.

이걸 JPA에서 표현하는 방법은 크게 두 가지였다:

- `Item`/`Trade`를 상위 타입으로 두고 `FixedPriceItem`/`AuctionItem`,
  `Purchase`/`Bid`로 서브클래스를 분리 (JOINED 또는 SINGLE_TABLE
  상속 전략)
- 서브클래스 없이 `Item`/`Trade` 단일 엔티티에 `saleType`/`tradeType`
  판별 컬럼을 두고, 타입별로만 의미 있는 필드는 nullable로 둠

## 결정

서브타입 상속 없이 단일 엔티티 + enum 판별 컬럼(`SaleType`,
`TradeType`)으로 설계했다. `Item.currentBidPrice`/`currentBidder`는
`FIXED_PRICE` 아이템에서는 항상 null이고, `Trade.status`가 갖는 값의
의미도 `PURCHASE`(즉시 `COMPLETED`)와 `BID`(`ACTIVE`→`OUTBID`)가
다르다.

## 고려한 대안

1. **JPA 상속 매핑(JOINED/SINGLE_TABLE)** — 타입별 필드를 깔끔하게
   분리할 수 있지만, 이 프로젝트 규모(Phase 1 얇은 수직 슬라이스)에서는
   과설계다. 검색/필터링 API(Elasticsearch 쪽에서 이미 처리할 영역)나
   서비스 로직 대부분이 `saleType`/`tradeType`으로 분기하는 정도라
   상속 계층이 주는 다형성 이점을 거의 못 살린다. JOINED 전략은 조회 시
   조인이 늘고, SINGLE_TABLE은 결국 지금 구조와 컬럼 구성이 거의
   같아진다.
2. **단일 테이블 + 판별 컬럼 (채택)** — nullable 컬럼이 늘어나는 대가로
   엔티티/리포지토리/서비스 코드가 단순해진다. "세 줄 비슷한 코드가
   섣부른 추상화보다 낫다"는 원칙에 맞춰, 지금 단계에서 실제로 반복되는
   타입별 분기가 크지 않으므로 상속 계층을 도입하지 않았다.

## 결과

- `Item.saleType`, `Trade.tradeType`이 비즈니스 로직 분기의 기준점이다
  (`TradeService.doPurchase`/`doBid`에서 `saleType` 불일치 시
  `InvalidTradeRequestException`).
- `Item`에 경매 전용 필드(`currentBidPrice`, `currentBidder`)가
  정가 아이템에도 컬럼으로 존재하지만 항상 null — DB 제약으로
  타입별 유효성을 강제하지는 않고, 애플리케이션 레벨 검증에 의존한다.
- 나중에 판매 방식이 늘어나거나(예: 즉시 구매+입찰 혼합) 타입별 필드가
  지금보다 훨씬 많아지면 이 결정을 재검토할 신호로 삼는다.
