/**
 * 두 백엔드에 대한 API 클라이언트와 응답 타입.
 *
 * 경로 접두사로 어느 서버인지 가른다 — vite.config.ts의 dev proxy가
 * `/api/backend` → 8080, `/api/ai` → 8000 으로 넘긴다. 브라우저는 단일
 * 오리진만 보므로 양쪽 서버에 CORS 설정이 필요 없다.
 */
import { TENANT } from "./demo";

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

async function request<T>(
  path: string,
  init: RequestInit & { userId?: number } = {},
): Promise<T> {
  const { userId, ...rest } = init;
  const headers = new Headers(rest.headers);
  headers.set("Content-Type", "application/json; charset=utf-8");
  // 백엔드만 헤더로 행위자를 식별한다. AI 서버는 본문의 tenant_code를 쓴다.
  if (path.startsWith("/api/backend")) {
    headers.set("X-Tenant-Id", String(TENANT.id));
    if (userId != null) headers.set("X-User-Id", String(userId));
  }

  const response = await fetch(path, { ...rest, headers });
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

export const api = {
  getItem: (itemId: number) => request<Item>(`/api/backend/items/${itemId}`),

  purchase: (itemId: number, quantity: number, userId: number) =>
    request<Trade>(`/api/backend/items/${itemId}/purchase`, {
      method: "POST",
      body: JSON.stringify({ quantity }),
      userId,
    }),

  bid: (itemId: number, bidPrice: number, userId: number) =>
    request<Trade>(`/api/backend/items/${itemId}/bids`, {
      method: "POST",
      body: JSON.stringify({ bidPrice }),
      userId,
    }),

  ask: (query: string, useCache = true) =>
    request<AssistantResponse>("/api/ai/assistant", {
      method: "POST",
      body: JSON.stringify({
        tenant_code: TENANT.code,
        query,
        use_cache: useCache,
      }),
    }),

  forecast: (itemId: number) =>
    request<Forecast>("/api/ai/forecast", {
      method: "POST",
      body: JSON.stringify({ tenant_code: TENANT.code, item_id: itemId }),
    }),

  alerts: (limit = 10) =>
    request<AlertQueue>(
      `/api/ai/anomaly/alerts?tenant_code=${TENANT.code}&limit=${limit}`,
    ),
};

export function formatWon(value: number): string {
  return `${Math.round(value).toLocaleString("ko-KR")}원`;
}
