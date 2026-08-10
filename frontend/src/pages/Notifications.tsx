import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { api, formatDateTime, type Notification } from "../api";

const TYPE_LABELS: Record<Notification["type"], string> = {
  PURCHASE_COMPLETED: "구매 완료",
  ITEM_SOLD: "판매 완료",
  BID_PLACED: "입찰",
  OUTBID: "상위 입찰 발생",
};

/**
 * 알림 목록 (ADR-0030 의 큐가 실제로 만든 것).
 *
 * 배지는 있었는데 **누를 데가 없었다** — 개수만 보이고 내용을 볼 방법이 없었다.
 * `api.notifications()` 는 그때도 있었지만 아무도 부르지 않았다.
 *
 * 읽음 처리는 **모두 읽음 하나뿐**이다. 개별 읽음은 화면에 그 동작이 없고,
 * 없는 동작을 위해 API 를 만들지 않는다.
 */
export default function Notifications() {
  const queryClient = useQueryClient();

  const notifications = useQuery({
    queryKey: ["notifications", "list"],
    queryFn: api.notifications,
  });

  const markRead = useMutation({
    mutationFn: api.markAllRead,
    // 목록의 read 표시와 헤더 배지가 **같이** 갱신돼야 한다. 하나만 무효화하면
    // 배지는 0인데 목록은 안 읽음으로 남는 상태가 보인다.
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["notifications"] });
    },
  });

  if (notifications.isPending)
    return <p className="muted">알림을 불러오는 중…</p>;
  if (notifications.isError)
    return <p className="error">{(notifications.error as Error).message}</p>;

  const unread = notifications.data.filter((item) => !item.read).length;

  return (
    <div className="stack">
      <div className="row">
        <h2 style={{ margin: 0 }}>알림</h2>
        {unread > 0 && (
          <span className="badge unread">
            안 읽음 <strong>{unread}건</strong>
          </span>
        )}
        <button
          type="button"
          disabled={unread === 0 || markRead.isPending}
          onClick={() => markRead.mutate()}
        >
          {markRead.isPending ? "처리 중…" : "모두 읽음"}
        </button>
      </div>

      {notifications.data.length === 0 ? (
        <p className="muted">
          알림이 없습니다. 구매나 입찰을 하면 체결 후 큐를 거쳐 도착합니다.
        </p>
      ) : (
        <div className="stack">
          {notifications.data.map((item) => (
            <div
              key={item.id}
              className="card"
              // 안 읽은 것을 굵게 하지 않고 왼쪽 띠로 표시한다 — 목록에서
              // 굵기는 이미 제목이 쓰고 있다.
              style={{
                borderLeft: item.read
                  ? "3px solid transparent"
                  : "3px solid var(--accent, #4a9eff)",
              }}
            >
              <div className="row">
                <strong>{TYPE_LABELS[item.type] ?? item.type}</strong>
                <span className="muted">
                  {formatDateTime(item.createdAt)}
                </span>
                {!item.read && <span className="badge unread">NEW</span>}
              </div>
              <div style={{ marginTop: 6 }}>{item.message}</div>
              <div className="muted" style={{ marginTop: 6 }}>
                {/* 거래 번호는 백엔드 거래다(합성 코퍼스가 아니다). 내역에서 찾을 수 있다. */}
                거래 #{item.tradeId} · <Link to="/trades">거래 내역에서 보기</Link>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
