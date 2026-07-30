/**
 * 데모용 테넌트·사용자 상수.
 *
 * 이 프로젝트에는 아직 인증이 없다. 백엔드는 `X-Tenant-Id`/`X-User-Id` 헤더로
 * 행위자를 식별하고, 코드에도 "인증(JWT) 도입 전까지의 임시 방편"으로 명시돼
 * 있다. 보안(per-tenant JWT claims, API Gateway, rate limiting)은 Phase 8
 * 항목이므로 여기서는 사용자를 드롭다운으로 전환하는 것으로 대신한다.
 *
 * 아래 id들은 `ai/scripts/export_demo_sql.py`가 넣은 행과 일치해야 한다.
 */

/**
 * 백엔드는 숫자 id(`X-Tenant-Id: 1`)를, AI 서버는 문자열 코드
 * (`tenant_code: "nexon"`)를 쓴다. 두 서버를 고치는 대신 프론트가 이 차이를
 * 흡수한다. 근본 통일은 Phase 8 정리 대상.
 */
export const TENANT = { id: 1, code: "nexon", name: "넥슨" } as const;

export interface DemoUser {
  id: number;
  username: string;
  role: "USER" | "ADMIN";
}

export const DEMO_USERS: DemoUser[] = [
  { id: 1, username: "gm_admin", role: "ADMIN" },
  { id: 2, username: "seller_kim", role: "USER" },
  { id: 3, username: "buyer_lee", role: "USER" },
  { id: 4, username: "trader_park", role: "USER" },
  { id: 5, username: "newbie_choi", role: "USER" },
];

/** 기본 선택 사용자. 판매자가 아닌 계정이라야 대부분의 아이템을 살 수 있다. */
export const DEFAULT_USER_ID = 3;
