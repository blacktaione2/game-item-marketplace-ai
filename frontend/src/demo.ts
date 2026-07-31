/**
 * 데모용 테넌트·사용자 상수.
 *
 * **이 값들은 더 이상 서버로 나가지 않는다.** 테넌트·행위자는 전부 JWT
 * 클레임에서 오고(ADR-0023), 여기 남은 건 화면 표시와 사용자 드롭다운의
 * 선택지뿐이다. 로그인 화면 대신 드롭다운으로 "누구로 접속했는가"를 고르면
 * `App.tsx`가 그 사용자의 토큰을 발급받는다.
 *
 * 아래 id들은 `ai/scripts/export_demo_sql.py`가 넣은 행과 일치해야 한다 —
 * 토큰 발급이 실제로 그 행을 조회하기 때문이다.
 */

/**
 * 백엔드는 숫자 id를, AI 서버는 문자열 코드(`"nexon"`)를 쓴다. 이제 **토큰이
 * 둘 다 싣고 다니므로** 프론트가 그 차이를 흡수하지 않는다. 여기 남은 값은
 * 화면 표기용(`name`)이다.
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
