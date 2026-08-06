-- 부하테스트 전용 데이터. **데모 시드(seed-demo.sql)와 분리한다.**
--
-- 왜 따로 필요한가
-- ----------------
-- 데모 아이템은 stock=10(고정가) / stock=1(경매)이다. 부하를 걸면 10요청 만에
-- 재고가 바닥나고 그 뒤로는 전부 invalid_request 거절이 된다. 그러면 락 대기가
-- 0에 수렴해서 **락 경합이 아니라 재고 고갈을 측정하게 된다.**
-- 실제로 관측성 라운드의 8건 스모크에서 2건이 재고 소진으로 거절됐다.
--
-- 입찰도 대안이 못 된다 — bidPrice > minimumAcceptableBid() 조건 때문에 동시
-- 입찰의 절반가량이 정상적으로 실패해서 역시 거절률을 재게 된다.
--
-- 그래서 재고가 사실상 마르지 않는 전용 아이템을 쓴다.
--
-- 아이템 id 대역
-- --------------
-- 9001~9020. 코퍼스(1~24, 101~118)와 겹치지 않는다. Elasticsearch에는 색인하지
-- 않는다 — 부하테스트는 백엔드를 직접 때리고 검색을 거치지 않는다.
--
-- **[2026-08-07] 위 문장의 전제가 깨졌다.** ADR-0037의 매물 목록 화면이
-- `GET /api/items`로 **Postgres를 직접 나열한다.** ES에 색인하지 않아도 이
-- 아이템들이 공개 데모의 첫 화면에 뜬다(42건 → 62건).
--
-- 그러므로 **공개 배포에 적용했다면 반드시 지운다:**
--   DELETE FROM trades WHERE item_id BETWEEN 9001 AND 9021;
--   DELETE FROM items  WHERE id      BETWEEN 9001 AND 9021;
--
-- 로컬에서는 상관없다. 이 주석이 필요한 이유는 **"검색에 안 뜬다"와 "화면에
-- 안 뜬다"가 더 이상 같은 말이 아니기 때문**이다.
--
-- 9001            : contended 프로파일. 전 VU가 이 하나에 몰린다
-- 9002~9021       : spread 프로파일. 부하를 20개로 분산해 대조군을 만든다
--
-- 적용:
--   docker exec -i gimp-postgres psql -U gimp -d gimp < seed-loadtest.sql

BEGIN;

-- 재실행 가능하게. FK 역순.
DELETE FROM trades WHERE item_id BETWEEN 9001 AND 9021;
DELETE FROM items WHERE id BETWEEN 9001 AND 9021;

-- 판매자는 데모 유저 2번(seller_kim)을 재사용한다. 구매자는 3~5번을 쓰므로
-- "본인 아이템 구매" 같은 규칙에 걸리지 않는다.
INSERT INTO items (
    id, tenant_id, seller_id, name, description, sale_type, price,
    current_bid_price, current_bidder_id, stock, status, version,
    created_at, updated_at
)
SELECT
    9001 + i,
    1,
    2,
    CASE WHEN i = 0 THEN '[부하] 경합 집중 아이템' ELSE '[부하] 분산 아이템 ' || i END,
    '부하테스트 전용. 데모 화면에 노출하지 말 것.',
    'FIXED_PRICE',
    10000.00,
    NULL,
    NULL,
    -- 재고가 마르면 측정이 무의미해진다. 여유 있게 잡는다.
    10000000,
    'ON_SALE',
    0,
    NOW(),
    NOW()
FROM generate_series(0, 20) AS i;

COMMIT;

-- 확인:
--   SELECT id, stock FROM items WHERE id BETWEEN 9001 AND 9021 ORDER BY id;
