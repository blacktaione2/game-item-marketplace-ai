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

/** Spring `Page` 의 직렬화 형태 중 **화면이 실제로 쓰는 필드만** 적는다. */
export interface Page<T> {
  content: T[];
  totalElements: number;
  totalPages: number;
  /** 0-based */
  number: number;
  size: number;
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
  /**
   * 시세·복합 분기가 질의에서 특정해낸 아이템.
   *
   * 두 분기에는 결과 그리드가 없어서, 이게 없으면 **어느 아이템 얘기인지
   * 확인하러 갈 방법이 없다.**
   *
   * 타입이 `SearchResultItem` 인 것이 요점이다 — 화면이 검색 결과와 **같은
   * 카드**로 그릴 수 있어야 같은 아이템이 화면마다 다르게 보이지 않는다.
   */
  resolved_item?: SearchResultItem;
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

/**
 * 거래 내역 한 줄. `Trade`(체결 응답)와 다르다 — 이쪽은 **보는 사람 기준**이라
 * `side`("내가 산 건가 판 건가")와 상대 이름이 들어 있고 id 대신 이름을 쓴다.
 */
export interface TradeHistoryEntry {
  id: number;
  itemId: number;
  itemName: string;
  tradeType: "PURCHASE" | "BID";
  status: string;
  price: number;
  quantity: number;
  side: "BUY" | "SELL";
  counterpartyUsername: string;
  createdAt: string;
}

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

/**
 * 토큰이 만료됐을 때 부를 콜백 (ADR-0035).
 *
 * ADR-0023 설계안에는 "만료 시 재발급 후 1회 재시도"가 있었는데, 로그인이 진짜
 * 비밀번호가 되면서(ADR-0031) **조용한 재발급이 불가능해졌다** — 비밀번호가 없으니
 * 다시 받을 수가 없다. 그런데 그 자리를 대체할 처리가 들어가지 않아, TTL 1시간이
 * 지나면 **모든 동작이 에러를 내고 사용자가 직접 로그아웃을 눌러야** 했다.
 */
let onSessionExpired: (() => void) | null = null;

export function setSessionExpiredHandler(handler: (() => void) | null): void {
  onSessionExpired = handler;
}

/**
 * 에러 본문에서 **사람이 읽을 문자열 하나**를 뽑는다. 항상 문자열이거나 null 이다 —
 * 호출부가 이 값을 JSX 에 그대로 렌더하기 때문이다.
 *
 * 형태가 셋이다.
 *   백엔드          `{ message: "…" }`
 *   AI 서버(HTTPException) `{ detail: "…" }`
 *   AI 서버(검증 422)      `{ detail: [{ msg, loc, … }, …] }`  ← 배열이다
 */
function normalizeErrorMessage(body: unknown): string | null {
  if (typeof body !== "object" || body === null) return null;
  const { detail, message } = body as { detail?: unknown; message?: unknown };

  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    // `input` 은 넣지 않는다 — 422 본문은 **입력 전체를 되돌려주므로** 그대로 쓰면
    // 500자짜리 오류 메시지가 화면에 박힌다.
    const reasons = detail
      .map((entry) => (typeof entry?.msg === "string" ? entry.msg : null))
      .filter((msg): msg is string => msg !== null);
    return reasons.length > 0 ? reasons.join(", ") : "입력값이 올바르지 않습니다.";
  }
  if (typeof message === "string") return message;
  return null;
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
    // **토큰을 들고 갔는데 401이면 세션이 죽은 것이다.** 토큰이 없는 상태의 401은
    // 로그인 실패이므로 건드리지 않는다 — 둘을 안 가르면 비밀번호를 틀릴 때마다
    // "세션 만료"가 뜬다. `accessToken` 유무가 그 구분이다.
    if (response.status === 401 && accessToken) {
      setAccessToken(null);
      onSessionExpired?.();
    }
    // 백엔드는 {message}, AI 서버는 {detail} 로 에러를 준다. 둘 다 흡수한다.
    //
    // **`detail` 이 문자열이라고 가정하면 안 된다.** FastAPI 의 검증 실패(422)는
    // `detail` 에 **객체 배열**을 담는다(`[{type, loc, msg, input}, …]`). 그대로
    // `message` 에 넣으면 `{error.message}` 를 렌더하는 순간 React 가
    // "Objects are not valid as a React child" 로 죽는다.
    //
    // 이 경로는 ADR-0035 이전에는 **도달할 수 없었다** — 422 를 낼 수 있는 필드가
    // UI 가 보내지 않는 `size` 뿐이었다. 질의 길이 상한을 걸면서 500자 초과를
    // 붙여넣는 경로가 처음 열렸고, 그때 이 가정이 드러났다.
    let message = `요청 실패 (HTTP ${response.status})`;
    try {
      const body = await response.json();
      message = normalizeErrorMessage(body) ?? message;
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

  /**
   * 매물 목록. **정렬·페이징을 서버에 맡긴다** (Spring `Pageable`).
   *
   * 클라이언트에서 전부 받아 정렬하는 쪽이 코드는 짧지만, 그건 "지금 42건"에만
   * 맞는 설계다. 엔드포인트가 이미 페이징을 하고 있으므로 그대로 쓴다.
   */
  items: (params: { page?: number; size?: number; sort?: string } = {}) => {
    const query = new URLSearchParams({
      page: String(params.page ?? 0),
      size: String(params.size ?? 20),
    });
    if (params.sort) query.set("sort", params.sort);
    return request<Page<Item>>(`/api/backend/items?${query}`);
  },

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

  markAllRead: () =>
    request<{ count: number }>("/api/backend/notifications/read", {
      method: "PATCH",
    }),

  // 조회자는 토큰에서 온다 — userId를 실어 보내지 않는다.
  trades: () => request<TradeHistoryEntry[]>("/api/backend/trades"),
};

export function formatWon(value: number): string {
  return `${Math.round(value).toLocaleString("ko-KR")}원`;
}
