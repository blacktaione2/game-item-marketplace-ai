/**
 * 두 백엔드에 대한 API 클라이언트와 응답 타입.
 *
 * 경로 접두사로 어느 서버인지 가른다 — vite.config.ts의 dev proxy가
 * `/api/backend` → 8080, `/api/ai` → 8000 으로 넘긴다. 브라우저는 단일
 * 오리진만 보므로 양쪽 서버에 CORS 설정이 필요 없다.
 *
 * 테넌트·행위자는 **요청에 싣지 않는다.** 둘 다 JWT 클레임에서 오고, 이 파일은
 * 토큰만 붙인다 (ADR-0023).
 */

// --- 백엔드(Spring Boot) 응답 타입 -----------------------------------------
export type SaleType = "FIXED_PRICE" | "AUCTION";
export type ItemStatus = "ON_SALE" | "SOLD_OUT" | "CLOSED";

export interface Item {
  id: number;
  tenantId: number;
  sellerId: number;
  sellerUsername: string;
  name: string;
  description: string;
  saleType: SaleType;
  price: number;
  currentBidPrice: number | null;
  currentBidderId: number | null;
  stock: number;
  status: ItemStatus;
  createdAt: string;
  updatedAt: string;
}

export interface Trade {
  id: number;
  itemId: number;
  buyerId: number;
  sellerId: number;
  tradeType: "PURCHASE" | "BID";
  price: number;
  quantity: number;
  status: string;
  createdAt: string;
}

// --- AI 서버(FastAPI) 응답 타입 --------------------------------------------
export interface SearchResultItem {
  item_id: number;
  name: string;
  category: string;
  /** 세부 종류(검/활/지팡이…). 하드 필터의 대상이자 결과 검증 수단. */
  subcategory: string;
  /** 속성(화염/냉기/…). `"무속성"`은 값 누락이 아니라 속성이 없다는 뜻. */
  element: string;
  price: number;
  enhancement_level: number;
  required_level: number;
  sale_type: SaleType;
}

export interface ToolCall {
  step: number;
  tool: string;
  arguments: Record<string, unknown>;
  failed: boolean;
}

export interface CacheInfo {
  hit: boolean;
  match_type?: "exact" | "semantic";
  similarity?: number;
  cached_query?: string;
}

export interface AssistantResponse {
  query: string;
  intent: string;
  routing: {
    decided_by: string;
    confidence: number | null;
    initial_intent: string;
  };
  answer: string;
  results?: SearchResultItem[];
  /** 검색이 0건이었음. LLM 없이 만들어진 확정 응답이다. */
  no_results?: boolean;
  /** 0건일 때만 온다 — 무슨 조건으로 찾았는지가 유일한 검증 근거라서. */
  conditions?: string[];
  applied_filters?: Record<string, string | number>;
  forecast?: Forecast;
  detection?: AnomalyAlert;
  tool_calls?: ToolCall[];
  tool_failures?: number;
  stop_reason?: string;
  llm_calls: number;
  cache: CacheInfo;
  timings: Record<string, number>;
}

export interface ForecastPoint {
  date: string;
  price: number;
  ratio: number;
}

export interface Forecast {
  item_id: number;
  name: string;
  category: string;
  cold_start: boolean;
  history_days: number;
  history: { date: string; price: number }[];
  anchor_price: number;
  horizon_days: number;
  forecast: ForecastPoint[];
  expected_change_pct: number;
  inherited_from: {
    item_id: number;
    name: string;
    category: string;
    similarity: number;
    weight: number;
  }[];
}

export interface LoginResult {
  token: string;
  expiresIn: number;
  userId: number;
  username: string;
  role: "USER" | "ADMIN";
}

export type NotificationType =
  | "PURCHASE_COMPLETED"
  | "ITEM_SOLD"
  | "BID_PLACED"
  | "OUTBID";

export interface Notification {
  id: number;
  tradeId: number;
  type: NotificationType;
  message: string;
  read: boolean;
  createdAt: string;
}

export interface Contribution {
  feature: string;
  share: number;
}

export interface AnomalyAlert {
  trade_id: number;
  item_id: number;
  buyer_id: number;
  seller_id: number;
  price: number;
  quantity: number;
  market_median: number;
  price_ratio: number;
  traded_at: string;
  anomaly_score: number;
  threshold: number;
  is_anomaly: boolean;
  contributions: Contribution[];
  injected_label: string | null;
  /**
   * 이 알림의 id들이 속한 데이터 평면.
   *
   * `synthetic`이면 trade_id·buyer_id·seller_id가 AI 코퍼스의 합성 값이고,
   * **백엔드 거래·유저 id와 범위가 겹치면서도 다른 대상을 가리킨다.**
   * 화면이 이걸 백엔드 id처럼 보여주면 사용자가 그 번호를 자기 거래 조회에
   * 입력하게 된다. 반드시 라벨을 붙여 구분할 것.
   */
  id_space: "synthetic" | "backend";
}

export interface AlertQueue {
  tenant_code: string;
  threshold: number;
  alert_percentile: number;
  total_trades: number;
  total_alerts: number;
  alerts: AnomalyAlert[];
}

// --- 호출 -------------------------------------------------------------------
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

/**
 * 발급받은 JWT. 모듈 변수로 두는 이유는 `request()`가 컴포넌트 밖이기 때문이고,
 * 새로고침하면 사라지는 게 맞다 — 데모 토큰이라 다시 받으면 그만이다.
 * localStorage에 두면 XSS 표면만 늘고 얻는 게 없다.
 */
let accessToken: string | null = null;

export function setAccessToken(token: string | null): void {
  accessToken = token;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Content-Type", "application/json; charset=utf-8");
  // **두 서버가 같은 토큰을 쓴다.** 예전에는 백엔드만 헤더로 행위자를 식별하고
  // AI 서버는 본문의 tenant_code를 봤는데, 그 tenant_code는 프론트가 자칭하는
  // 값이었다. 이제 테넌트도 토큰 클레임에서 온다 (ADR-0023).
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);

  const response = await fetch(path, { ...init, headers });
  if (!response.ok) {
    // 백엔드는 {message}, AI 서버는 {detail} 로 에러를 준다. 둘 다 흡수한다.
    let message = `요청 실패 (HTTP ${response.status})`;
    try {
      const body = await response.json();
      message = body.detail ?? body.message ?? message;
    } catch {
      /* 본문이 JSON이 아니면 기본 메시지를 쓴다 */
    }
    throw new ApiError(response.status, message);
  }
  return response.json() as Promise<T>;
}

export interface DemoToken {
  token: string;
  expiresIn: number;
  userId: number;
  username: string;
  role: string;
}

export const api = {
  /**
   * 로그인 (ADR-0031, 테넌트는 ADR-0034). `demo-token` 을 대체한다 — 그건 비밀번호를
   * 확인하지 않아 userId 만 알면 누구나 그 사용자가 될 수 있었다.
   *
   * userId 가 아니라 username+password 를 보낸다. 신원을 요청이 주장하지 않는다.
   *
   * **`tenantCode` 가 함께 간다.** 아이디는 테넌트 안에서만 유일해서(제약이
   * `(tenant_id, username)`) 아이디 하나로는 계정이 특정되지 않는다. 이건 로그인
   * **한 곳뿐**이다 — 발급 이후의 요청은 여전히 테넌트를 싣지 않고 토큰에서 읽는다.
   */
  login: (tenantCode: string, username: string, password: string) =>
    request<LoginResult>("/api/backend/auth/login", {
      method: "POST",
      body: JSON.stringify({ tenantCode, username, password }),
    }),

  getItem: (itemId: number) => request<Item>(`/api/backend/items/${itemId}`),

  // 구매자·입찰자는 토큰에서 온다 — 더 이상 프론트가 지목하지 않는다.
  purchase: (itemId: number, quantity: number) =>
    request<Trade>(`/api/backend/items/${itemId}/purchase`, {
      method: "POST",
      body: JSON.stringify({ quantity }),
    }),

  bid: (itemId: number, bidPrice: number) =>
    request<Trade>(`/api/backend/items/${itemId}/bids`, {
      method: "POST",
      body: JSON.stringify({ bidPrice }),
    }),

  // AI 서버 요청에서도 tenant_code가 빠졌다 — 토큰 클레임이 출처다.
  ask: (query: string, useCache = true) =>
    request<AssistantResponse>("/api/ai/assistant", {
      method: "POST",
      body: JSON.stringify({ query, use_cache: useCache }),
    }),

  forecast: (itemId: number) =>
    request<Forecast>("/api/ai/forecast", {
      method: "POST",
      body: JSON.stringify({ item_id: itemId }),
    }),

  alerts: (limit = 10) =>
    request<AlertQueue>(`/api/ai/anomaly/alerts?limit=${limit}`),

  // 알림은 체결 후 **비동기로** 만들어진다(ADR-0030). 구매 직후 조회하면
  // 아직 0건일 수 있다 — 그게 정상이고, 큐가 비면 채워진다.
  notifications: () => request<Notification[]>("/api/backend/notifications"),

  unreadCount: () =>
    request<{ count: number }>("/api/backend/notifications/unread-count"),
};

export function formatWon(value: number): string {
  return `${Math.round(value).toLocaleString("ko-KR")}원`;
}
