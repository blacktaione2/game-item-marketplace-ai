import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { api, formatDateTime, formatWon, type TradeHistoryEntry } from "../api";

/**
 * 내 거래 내역.
 *
 * 구매·입찰이 성공해도 **그 화면을 떠나면 확인할 방법이 없었다.** 체결 결과가
 * 상세 화면의 지역 상태로만 남아서, 새로고침 한 번에 사라진다.
 *
 * 산 것과 판 것을 한 목록에 둔다. 거래소 사용자는 양쪽을 다 하고, 나누면
 * "오늘 무슨 일이 있었나"를 보려고 두 탭을 오가게 된다. 대신 `side` 배지로
 * 방향을 표시한다.
 */
export default function Trades() {
  const trades = useQuery({
    queryKey: ["trades"],
    queryFn: api.trades,
  });

  if (trades.isPending) return <p className="muted">거래 내역을 불러오는 중…</p>;
  if (trades.isError)
    return <p className="error">{(trades.error as Error).message}</p>;

  if (trades.data.length === 0) {
    return (
      <div className="stack">
        <h2 style={{ margin: 0 }}>거래 내역</h2>
        <p className="muted">아직 거래가 없습니다. 검색해서 아이템을 사보세요.</p>
        <Link to="/" className="muted">
          검색으로 →
        </Link>
      </div>
    );
  }

  return (
    <div className="stack">
      <div className="row">
        <h2 style={{ margin: 0 }}>거래 내역</h2>
        <span className="badge">
          전체 <strong>{trades.data.length}건</strong>
        </span>
      </div>

      <div className="card" style={{ overflowX: "auto" }}>
        <table>
          <thead>
            <tr>
              <th>구분</th>
              <th>아이템</th>
              <th>상대</th>
              <th style={{ textAlign: "right" }}>금액</th>
              <th>상태</th>
              <th>일시</th>
            </tr>
          </thead>
          <tbody>
            {trades.data.map((trade) => (
              <TradeRow key={trade.id} trade={trade} />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function TradeRow({ trade }: { trade: TradeHistoryEntry }) {
  const bought = trade.side === "BUY";
  return (
    <tr>
      <td>
        <span className={`badge ${bought ? "" : "hit"}`}>
          {bought ? "구매" : "판매"}
          {trade.tradeType === "BID" && " · 입찰"}
        </span>
      </td>
      <td>
        {/* 아이템 상세로 돌아갈 수 있어야 내역이 쓸모 있다. */}
        <Link to={`/items/${trade.itemId}`}>{trade.itemName}</Link>
      </td>
      <td className="muted">{trade.counterpartyUsername}</td>
      <td style={{ textAlign: "right" }}>
        {formatWon(trade.price)}
        {trade.quantity > 1 && (
          <span className="muted"> × {trade.quantity}</span>
        )}
      </td>
      <td className="muted">{trade.status}</td>
      <td className="muted">
        {formatDateTime(trade.createdAt)}
      </td>
    </tr>
  );
}
