-- 자동 생성 파일 — 직접 수정하지 말 것.
-- 생성: python -m scripts.export_demo_sql  (ai/ 에서 실행)
-- 아이템 정의는 ai/app/corpus 가 단일 진실 공급원이며,
-- ES 색인(scripts.seed_items)과 같은 데이터에서 나온다.

BEGIN;

-- 재실행 가능하게 기존 데모 데이터를 먼저 지운다.
-- trades → items → users → tenants 순서 (FK 역순).
DELETE FROM trades WHERE tenant_id = 1;
DELETE FROM items WHERE tenant_id = 1;
DELETE FROM users WHERE tenant_id = 1;
DELETE FROM tenants WHERE id = 1;

INSERT INTO tenants (id, code, name, created_at, updated_at) VALUES
  (1, 'nexon', '넥슨', NOW(), NOW());

INSERT INTO users (id, tenant_id, username, email, password_hash, role, created_at, updated_at) VALUES
  (1, 1, 'gm_admin', 'gm_admin@example.com', '$2a$10$demoDemoDemoDemoDemoDe', 'ADMIN', NOW(), NOW()),
  (2, 1, 'seller_kim', 'seller_kim@example.com', '$2a$10$demoDemoDemoDemoDemoDe', 'USER', NOW(), NOW()),
  (3, 1, 'buyer_lee', 'buyer_lee@example.com', '$2a$10$demoDemoDemoDemoDemoDe', 'USER', NOW(), NOW()),
  (4, 1, 'trader_park', 'trader_park@example.com', '$2a$10$demoDemoDemoDemoDemoDe', 'USER', NOW(), NOW()),
  (5, 1, 'newbie_choi', 'newbie_choi@example.com', '$2a$10$demoDemoDemoDemoDemoDe', 'USER', NOW(), NOW());

INSERT INTO items (id, tenant_id, seller_id, name, description, sale_type, price, current_bid_price, current_bidder_id, stock, status, version, created_at, updated_at) VALUES
  (1, 1, 2, '+9 강화 롱소드', '공격력 +120, 치명타 확률 8% 증가. 강화 실패 이력 없음.', 'FIXED_PRICE', 45000.00, NULL, NULL, 10, 'ON_SALE', 0, NOW(), NOW()),
  (2, 1, 4, '+8 강화 롱소드', '공격력 +95, 치명타 확률 5% 증가. 무난한 중급 무기.', 'FIXED_PRICE', 28000.00, NULL, NULL, 10, 'ON_SALE', 0, NOW(), NOW()),
  (3, 1, 5, '+7 강화 롱소드', '공격력 +70. 입문자용 강화 무기.', 'FIXED_PRICE', 15000.00, NULL, NULL, 10, 'ON_SALE', 0, NOW(), NOW()),
  (4, 1, 2, '전설 등급 대검', '공격력 +200, 광역 피해 증가. 획득 난이도 최상.', 'AUCTION', 300000.00, 300000.00, NULL, 1, 'ON_SALE', 0, NOW(), NOW()),
  (5, 1, 4, '미스릴 단검', '공격 속도가 매우 빠른 단검. 암살자 직업 전용.', 'FIXED_PRICE', 22000.00, NULL, NULL, 10, 'ON_SALE', 0, NOW(), NOW()),
  (6, 1, 5, '+9 강화 미스릴 갑옷', '방어력 +150, 모든 속성 저항 10% 증가.', 'FIXED_PRICE', 52000.00, NULL, NULL, 10, 'ON_SALE', 0, NOW(), NOW()),
  (7, 1, 2, '용비늘 방패', '방어력 +180, 화염 피해 40% 감소. 보스 레이드 추천.', 'AUCTION', 175000.00, 175000.00, NULL, 1, 'ON_SALE', 0, NOW(), NOW()),
  (8, 1, 4, '가죽 경갑', '방어력 +40, 이동 속도 감소 없음. 초보자용.', 'FIXED_PRICE', 5000.00, NULL, NULL, 10, 'ON_SALE', 0, NOW(), NOW()),
  (9, 1, 5, '현자의 반지', '마력 +80, 스킬 재사용 대기시간 12% 감소.', 'FIXED_PRICE', 67000.00, NULL, NULL, 10, 'ON_SALE', 0, NOW(), NOW()),
  (10, 1, 2, '행운의 목걸이', '아이템 획득 확률 15% 증가. 파밍 필수템.', 'AUCTION', 89000.00, 89000.00, NULL, 1, 'ON_SALE', 0, NOW(), NOW()),
  (11, 1, 4, '체력 회복 물약 100개 묶음', '사용 시 체력 500 즉시 회복. 대량 판매.', 'FIXED_PRICE', 3000.00, NULL, NULL, 10, 'ON_SALE', 0, NOW(), NOW()),
  (12, 1, 5, '강화 주문서 (성공률 70%)', '장비 강화 시 성공 확률을 높여주는 소모성 주문서.', 'FIXED_PRICE', 12000.00, NULL, NULL, 10, 'ON_SALE', 0, NOW(), NOW()),
  (13, 1, 2, '만렙 전사 계정', '레벨 200 전사, 전설 장비 풀세팅 포함. 본인 인증 완료.', 'AUCTION', 850000.00, 850000.00, NULL, 1, 'ON_SALE', 0, NOW(), NOW()),
  (14, 1, 4, '게임 머니 1000만 골드', '즉시 거래 가능한 인게임 재화.', 'FIXED_PRICE', 40000.00, NULL, NULL, 10, 'ON_SALE', 0, NOW(), NOW()),
  (15, 1, 5, '불꽃의 마법봉', '화염 계열 마법 위력 25% 증가. 마법사 직업 전용 무기.', 'FIXED_PRICE', 33000.00, NULL, NULL, 10, 'ON_SALE', 0, NOW(), NOW()),
  (16, 1, 2, '얼음 서리 지팡이', '냉기 마법 위력 30% 증가, 적 이동 속도 둔화 효과.', 'AUCTION', 78000.00, 78000.00, NULL, 1, 'ON_SALE', 0, NOW(), NOW()),
  (17, 1, 4, '+9 강화 사슬 갑옷', '방어력 +130, 물리 피해 감소 20%.', 'FIXED_PRICE', 41000.00, NULL, NULL, 10, 'ON_SALE', 0, NOW(), NOW()),
  (18, 1, 5, '신속의 장화', '이동 속도 20% 증가, 회피율 5% 증가.', 'FIXED_PRICE', 19000.00, NULL, NULL, 10, 'ON_SALE', 0, NOW(), NOW()),
  (19, 1, 2, '고대 유물 파편', '전설 장비 제작에 필요한 희귀 재료. 거래량이 적음.', 'AUCTION', 125000.00, 125000.00, NULL, 1, 'ON_SALE', 0, NOW(), NOW()),
  (20, 1, 4, '암흑 로브', '마력 +110, 암흑 속성 저항 25%. 흑마법사 전용 방어구.', 'FIXED_PRICE', 58000.00, NULL, NULL, 10, 'ON_SALE', 0, NOW(), NOW()),
  (21, 1, 5, '바람의 장궁', '원거리 공격력 +140, 사거리 증가. 레벨 100 이상 궁수 착용 가능.', 'FIXED_PRICE', 62000.00, NULL, NULL, 10, 'ON_SALE', 0, NOW(), NOW()),
  (22, 1, 2, '+9 강화 사냥꾼의 활', '원거리 공격력 +95, 치명타 확률 10%. 레벨 80 이상 착용 가능.', 'FIXED_PRICE', 48000.00, NULL, NULL, 10, 'ON_SALE', 0, NOW(), NOW()),
  (23, 1, 4, '초심자용 단궁', '원거리 공격력 +30. 레벨 10 이상 착용 가능한 입문용 활.', 'FIXED_PRICE', 4000.00, NULL, NULL, 10, 'ON_SALE', 0, NOW(), NOW()),
  (24, 1, 5, '불꽃의 대검', '화염 속성 피해 +90, 공격 시 화상 효과. 불속성 무기.', 'AUCTION', 143000.00, 143000.00, NULL, 1, 'ON_SALE', 0, NOW(), NOW()),
  (101, 1, 2, '+8 강화 스틸 해머', '공격력 +105, 방어 관통 12%. 둔기 계열 중급 무기.', 'FIXED_PRICE', 31000.00, NULL, NULL, 10, 'ON_SALE', 0, NOW(), NOW()),
  (102, 1, 4, '+6 강화 스틸 해머', '공격력 +72, 방어 관통 7%. 강화 단계가 낮은 둔기.', 'FIXED_PRICE', 17000.00, NULL, NULL, 10, 'ON_SALE', 0, NOW(), NOW()),
  (103, 1, 5, '그림자 암살검', '치명타 피해 +45%, 은신 중 공격력 증가. 도적 직업 전용.', 'AUCTION', 118000.00, 118000.00, NULL, 1, 'ON_SALE', 0, NOW(), NOW()),
  (104, 1, 2, '성기사의 워메이스', '공격력 +130, 신성 피해 추가. 성기사 전용 둔기.', 'FIXED_PRICE', 74000.00, NULL, NULL, 10, 'ON_SALE', 0, NOW(), NOW()),
  (105, 1, 4, '폭풍의 창', '공격력 +115, 번개 속성 피해 추가. 사거리가 긴 창.', 'AUCTION', 96000.00, 96000.00, NULL, 1, 'ON_SALE', 0, NOW(), NOW()),
  (106, 1, 5, '수정 마법구', '마력 +140, 마나 소모 15% 감소. 마법사 직업 전용 보조 무기.', 'FIXED_PRICE', 68000.00, NULL, NULL, 10, 'ON_SALE', 0, NOW(), NOW()),
  (107, 1, 2, '용의 숨결 지팡이', '화염 마법 위력 40% 증가, 시전 속도 증가. 불 속성 특화 지팡이.', 'AUCTION', 152000.00, 152000.00, NULL, 1, 'ON_SALE', 0, NOW(), NOW()),
  (108, 1, 4, '사냥꾼의 쇠뇌', '원거리 공격력 +110, 관통 사격 가능. 석궁 계열 무기.', 'FIXED_PRICE', 54000.00, NULL, NULL, 10, 'ON_SALE', 0, NOW(), NOW()),
  (109, 1, 5, '티타늄 판금 갑옷', '방어력 +195, 물리 피해 감소 28%. 중갑 계열 최상급.', 'AUCTION', 188000.00, 188000.00, NULL, 1, 'ON_SALE', 0, NOW(), NOW()),
  (110, 1, 2, '+7 강화 은빛 사슬갑', '방어력 +98, 마법 저항 15% 증가.', 'FIXED_PRICE', 36000.00, NULL, NULL, 10, 'ON_SALE', 0, NOW(), NOW()),
  (111, 1, 4, '+9 강화 은빛 사슬갑', '방어력 +142, 마법 저항 22% 증가. 고강화 중갑.', 'FIXED_PRICE', 59000.00, NULL, NULL, 10, 'ON_SALE', 0, NOW(), NOW()),
  (112, 1, 5, '마도사의 후드', '마력 +95, 스킬 시전 속도 10% 증가. 천 계열 방어구.', 'FIXED_PRICE', 47000.00, NULL, NULL, 10, 'ON_SALE', 0, NOW(), NOW()),
  (113, 1, 2, '강철 각반', '방어력 +55, 넉백 저항 증가. 입문용 하의 방어구.', 'FIXED_PRICE', 8000.00, NULL, NULL, 10, 'ON_SALE', 0, NOW(), NOW()),
  (114, 1, 4, '수호자의 팔찌', '받는 피해 8% 감소, 최대 체력 +300.', 'FIXED_PRICE', 72000.00, NULL, NULL, 10, 'ON_SALE', 0, NOW(), NOW()),
  (115, 1, 5, '심연의 귀걸이', '암흑 속성 피해 +60, 흡혈 효과 5%. 희귀 장신구.', 'AUCTION', 134000.00, 134000.00, NULL, 1, 'ON_SALE', 0, NOW(), NOW()),
  (116, 1, 2, '마나 회복 물약 50개 묶음', '사용 시 마나 300 즉시 회복. 묶음 판매.', 'FIXED_PRICE', 6000.00, NULL, NULL, 10, 'ON_SALE', 0, NOW(), NOW()),
  (117, 1, 4, '축복의 주문서 (성공률 90%)', '장비에 추가 옵션을 부여하는 고급 주문서.', 'AUCTION', 88000.00, 88000.00, NULL, 1, 'ON_SALE', 0, NOW(), NOW()),
  (118, 1, 5, '레벨 150 궁수 계정', '레벨 150 궁수, 희귀 활 3종 보유. 본인 인증 완료.', 'AUCTION', 620000.00, 620000.00, NULL, 1, 'ON_SALE', 0, NOW(), NOW());

-- 명시적 id로 넣었으므로 시퀀스를 최대값 뒤로 옮긴다.
SELECT setval(pg_get_serial_sequence('tenants', 'id'), (SELECT MAX(id) FROM tenants));
SELECT setval(pg_get_serial_sequence('users', 'id'), (SELECT MAX(id) FROM users));
SELECT setval(pg_get_serial_sequence('items', 'id'), (SELECT MAX(id) FROM items));

COMMIT;
