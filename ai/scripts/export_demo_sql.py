"""데모용 PostgreSQL 시드 SQL을 코퍼스에서 생성한다.

실행: python -m scripts.export_demo_sql
적용: docker exec -i gimp-postgres psql -U gimp -d gimp < backend/src/main/resources/db/seed-demo.sql

## 왜 필요한가

AI 서버와 백엔드가 서로 다른 저장소를 본다.

| 저장소 | 내용 |
|---|---|
| Elasticsearch | 아이템 42건 (검색·시세예측이 쓴다) |
| PostgreSQL    | 거래 트랜잭션의 소스 오브 트루스 |

Phase 7 이전까지 Postgres는 비어 있었다. 그래서 `검색 → 상세 → 구매`
경로가 끊긴다 — 검색이 ES에서 `item_id=24`를 돌려줘도 백엔드에는 그 아이템도,
구매자도, 테넌트도 없다.

**item_id를 양쪽에서 같게 맞추는 것이 이 스크립트의 핵심이다.** 그래서 SQL을
손으로 쓰지 않고 ES 색인과 **같은 코퍼스**에서 생성한다. 아이템을 추가하면
`seed_items`와 이 스크립트를 다시 돌리면 되고, 두 저장소가 어긋날 일이 없다.

## 스키마 차이

Postgres `items`에는 `enhancement_level` / `required_level` 컬럼이 **없다**
(ES 매핑에만 있다). 화면을 위해 백엔드 스키마를 늘리지 않는다 — 거래 상태는
Postgres가, 검색용 속성은 ES가 갖는 현재 분담을 유지한다.
"""

from __future__ import annotations

from pathlib import Path

from app.corpus import ALL_ITEMS

OUTPUT = (
    Path(__file__).resolve().parents[2]
    / "backend"
    / "src"
    / "main"
    / "resources"
    / "db"
    / "seed-demo.sql"
)

TENANT = {"id": 1, "code": "nexon", "name": "넥슨"}

# 데모 사용자. 프론트의 사용자 전환 드롭다운이 이 id들을 그대로 X-User-Id로 쓴다.
# 판매자와 구매자를 겸하게 두어 어느 계정으로 로그인해도 시연이 가능하다.
USERS = [
    {"id": 1, "username": "gm_admin", "role": "ADMIN"},
    {"id": 2, "username": "seller_kim", "role": "USER"},
    {"id": 3, "username": "buyer_lee", "role": "USER"},
    {"id": 4, "username": "trader_park", "role": "USER"},
    {"id": 5, "username": "newbie_choi", "role": "USER"},
]

# 판매자를 유저 여러 명에게 분산시킨다 — 전부 한 사람이 판다고 나오면
# "내 아이템은 못 산다" 같은 규칙을 화면에서 확인할 수 없다.
SELLER_IDS = [2, 4, 5]


def sql_escape(value: str) -> str:
    return value.replace("'", "''")


def build() -> str:
    lines: list[str] = [
        "-- 자동 생성 파일 — 직접 수정하지 말 것.",
        "-- 생성: python -m scripts.export_demo_sql  (ai/ 에서 실행)",
        "-- 아이템 정의는 ai/app/corpus 가 단일 진실 공급원이며,",
        "-- ES 색인(scripts.seed_items)과 같은 데이터에서 나온다.",
        "",
        "BEGIN;",
        "",
        "-- 재실행 가능하게 기존 데모 데이터를 먼저 지운다.",
        "-- trades → items → users → tenants 순서 (FK 역순).",
        "DELETE FROM trades WHERE tenant_id = {tenant};".format(tenant=TENANT["id"]),
        "DELETE FROM items WHERE tenant_id = {tenant};".format(tenant=TENANT["id"]),
        "DELETE FROM users WHERE tenant_id = {tenant};".format(tenant=TENANT["id"]),
        "DELETE FROM tenants WHERE id = {tenant};".format(tenant=TENANT["id"]),
        "",
        "INSERT INTO tenants (id, code, name, created_at, updated_at) VALUES",
        "  ({id}, '{code}', '{name}', NOW(), NOW());".format(**TENANT),
        "",
        "INSERT INTO users (id, tenant_id, username, email, password_hash, role, created_at, updated_at) VALUES",
    ]

    user_rows = [
        "  ({id}, {tenant}, '{username}', '{username}@example.com', "
        "'$2a$10$demoDemoDemoDemoDemoDe', '{role}', NOW(), NOW())".format(
            tenant=TENANT["id"], **user
        )
        for user in USERS
    ]
    lines.append(",\n".join(user_rows) + ";")
    lines.append("")

    lines.append(
        "INSERT INTO items (id, tenant_id, seller_id, name, description, sale_type, "
        "price, current_bid_price, current_bidder_id, stock, status, version, "
        "created_at, updated_at) VALUES"
    )

    item_rows = []
    for index, item in enumerate(ALL_ITEMS):
        is_auction = item["sale_type"] == "AUCTION"
        item_rows.append(
            "  ({item_id}, {tenant}, {seller}, '{name}', '{description}', "
            "'{sale_type}', {price:.2f}, {bid}, NULL, {stock}, 'ON_SALE', 0, "
            "NOW(), NOW())".format(
                item_id=item["item_id"],
                tenant=TENANT["id"],
                seller=SELLER_IDS[index % len(SELLER_IDS)],
                name=sql_escape(item["name"]),
                description=sql_escape(item["description"]),
                sale_type=item["sale_type"],
                price=float(item["price"]),
                # 경매는 시작가를 현재 입찰가로 둔다 — 입찰 화면이 비교 대상을
                # 가지려면 초기값이 있어야 한다.
                bid=f"{float(item['price']):.2f}" if is_auction else "NULL",
                # 고정가는 재고 개념이 있고 경매는 1점물이다.
                stock=1 if is_auction else 10,
            )
        )
    lines.append(",\n".join(item_rows) + ";")
    lines.append("")

    # 시퀀스를 수동 id 뒤로 밀어둔다. 안 하면 다음 INSERT가 id=1을 다시 써서
    # 중복 키로 죽는다 — 시드 데이터를 명시적 id로 넣을 때의 전형적인 함정.
    lines += [
        "-- 명시적 id로 넣었으므로 시퀀스를 최대값 뒤로 옮긴다.",
        "SELECT setval(pg_get_serial_sequence('tenants', 'id'), "
        "(SELECT MAX(id) FROM tenants));",
        "SELECT setval(pg_get_serial_sequence('users', 'id'), "
        "(SELECT MAX(id) FROM users));",
        "SELECT setval(pg_get_serial_sequence('items', 'id'), "
        "(SELECT MAX(id) FROM items));",
        "",
        "COMMIT;",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(build(), encoding="utf-8")
    print(f"생성: {OUTPUT}")
    print(f"  테넌트 1 / 유저 {len(USERS)} / 아이템 {len(ALL_ITEMS)}")
    print("적용: docker exec -i gimp-postgres psql -U gimp -d gimp < " f"{OUTPUT}")


if __name__ == "__main__":
    main()
