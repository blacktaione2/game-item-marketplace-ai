import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Suspense, lazy, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { api, formatWon, type Trade } from "../api";

/**
 * 차트만 지연 로딩한다 (ADR-0027).
 *
 * Recharts는 번들의 대부분인데 **이 화면에서만** 쓰인다. 랜딩 페이지(검색)가
 * 쓰지도 않는 차트 라이브러리를 받고 있었다.
 *
 * 라우트 단위가 아니라 컴포넌트 단위인 이유: `ItemDetail`·`AnomalyQueue`
 * 자체 코드는 작아서 라우트를 쪼개도 추가 이득이 거의 없고, Suspense 경계만
 * 세 곳으로 늘어난다.
 */
const PriceChart = lazy(() => import("../components/PriceChart"));

// userId는 이제 요청에 안 실린다(토큰이 행위자를 정한다). 화면 표시용으로만
// 남는다 — "내가 올린 매물"인지 판단하는 데 쓴다.
export default function ItemDetail({ userId }: { userId: number }) {
  const { itemId } = useParams();
  const id = Number(itemId);
  const queryClient = useQueryClient();
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);

  const item = useQuery({
    queryKey: ["item", id],
    queryFn: () => api.getItem(id),
  });

  const forecast = useQuery({
    queryKey: ["forecast", id],
    queryFn: () => api.forecast(id),
  });

  // 청크 다운로드와 시세 조회는 **서로 독립**이다. 차트가 실제로 그려지는 건
  // 예측이 도착한 뒤인데, 그때 청크를 받기 시작하면 두 대기가 직렬로 붙는다.
  // 마운트 시 미리 받아두면 폴백이 보일 일이 거의 없다 — 지연 로딩으로 생기는
  // 유일한 UX 변화를 그 자리에서 상쇄한다.
  useEffect(() => {
    void import("../components/PriceChart");
  }, []);

  // 낙관적 업데이트를 하지 않는다. Redis 분산 락 경합으로 실패할 수 있고,
  // 그 실패가 이 프로젝트가 보여주려는 동작이라 그대로 드러내야 한다.
  const trade = useMutation({
    mutationFn: (input: { kind: "purchase" | "bid"; amount: number }) =>
      input.kind === "purchase"
        ? api.purchase(id, input.amount)
        : api.bid(id, input.amount),
    onSuccess: (data: Trade) => {
      setResult({
        ok: true,
        text: `${data.tradeType === "PURCHASE" ? "구매" : "입찰"} 성공 — 거래 #${data.id}, ${formatWon(data.price)}`,
      });
      queryClient.invalidateQueries({ queryKey: ["item", id] });
    },
    onError: (error: Error) => setResult({ ok: false, text: error.message }),
  });

  if (item.isPending) return <p className="muted">불러오는 중…</p>;
  if (item.isError) return <p className="error">{(item.error as Error).message}</p>;

  const data = item.data;
  const isAuction = data.saleType === "AUCTION";
  const nextBid = Math.round((data.currentBidPrice ?? data.price) * 1.05);

  return (
    <div className="stack">
      <Link to="/" className="muted">
        ← 검색으로
      </Link>

      <div className="card stack">
        <div>
          <h2 style={{ margin: "0 0 4px" }}>{data.name}</h2>
          <div className="muted">
            판매자 {data.sellerUsername} · {isAuction ? "경매" : "고정가"} ·{" "}
            {data.status} · 재고 {data.stock}
          </div>
        </div>
        <p style={{ margin: 0 }}>{data.description}</p>
        <div className="price">
          {isAuction
            ? `현재 입찰가 ${formatWon(data.currentBidPrice ?? data.price)}`
            : formatWon(data.price)}
        </div>

        <div className="row">
          {isAuction ? (
            <button
              className="primary"
              disabled={trade.isPending || data.status !== "ON_SALE"}
              onClick={() => trade.mutate({ kind: "bid", amount: nextBid })}
            >
              {formatWon(nextBid)}에 입찰
            </button>
          ) : (
            <button
              className="primary"
              disabled={trade.isPending || data.stock < 1 || data.status !== "ON_SALE"}
              onClick={() => trade.mutate({ kind: "purchase", amount: 1 })}
            >
              1개 구매
            </button>
          )}
          {data.sellerId === userId && (
            <span className="muted">내가 등록한 아이템입니다.</span>
          )}
        </div>

        {result && (
          <p className={result.ok ? "muted" : "error"} style={{ margin: 0 }}>
            {result.text}
          </p>
        )}
      </div>

      <div className="card stack">
        <div className="row">
          <strong>시세 예측</strong>
          {forecast.data?.cold_start && (
            <span className="badge warn">
              Cold Start — 거래 이력 부족, 유사 아이템 추세 상속
            </span>
          )}
          {forecast.data && (
            <span className="badge">
              D+{forecast.data.horizon_days} 예상{" "}
              <strong>
                {forecast.data.expected_change_pct > 0 ? "+" : ""}
                {forecast.data.expected_change_pct}%
              </strong>
            </span>
          )}
        </div>

        {forecast.isPending && <p className="muted">예측 계산 중…</p>}
        {forecast.isError && (
          <p className="error">{(forecast.error as Error).message}</p>
        )}

        {forecast.data && (
          <>
            {/* 폴백 높이를 차트와 같은 260px로 맞춘다. 다르면 청크가 도착하는
                순간 아래 내용이 밀려 레이아웃이 튄다. */}
            <Suspense
              fallback={
                <div
                  className="muted"
                  style={{ height: 260, display: "flex", alignItems: "center" }}
                >
                  차트 불러오는 중…
                </div>
              }
            >
              <PriceChart forecast={forecast.data} />
            </Suspense>
            <div className="muted">
              예측 기준가 {formatWon(forecast.data.anchor_price)} ·{" "}
              {/* 등록가와 기준가는 성격이 다르다. 둘을 나란히 두고 빼서
                  읽으면 안 된다는 걸 Phase 6 에이전트에서 실제로 겪었다. */}
              거래 이력 {forecast.data.history_days}일
              {" · "}
              위 등록가({formatWon(data.price)})와는 기준이 다릅니다
            </div>
            {forecast.data.inherited_from.length > 0 && (
              <div className="muted">
                추세 상속 출처:{" "}
                {forecast.data.inherited_from
                  .map((source) => `${source.name} (${Math.round(source.weight * 100)}%)`)
                  .join(", ")}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
