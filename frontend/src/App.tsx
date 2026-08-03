import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";

import { api, setAccessToken } from "./api";
import { DEFAULT_USER_ID, DEMO_USERS, TENANT } from "./demo";
import AnomalyQueue from "./pages/AnomalyQueue";
import Assistant from "./pages/Assistant";
import ItemDetail from "./pages/ItemDetail";

export default function App() {
  // 로그인 화면이 없으므로 "누구로 접속했는가"를 드롭다운으로 고른다. 다만
  // 선택 결과가 그대로 헤더로 나가던 예전과 달리, 이제는 **서버가 그 사용자의
  // 토큰을 발급**하고 이후 요청은 전부 그 토큰으로 나간다 (ADR-0023).
  const [userId, setUserId] = useState(DEFAULT_USER_ID);
  const [tokenState, setTokenState] = useState<"loading" | "ready" | "error">(
    "loading",
  );
  const currentUser = DEMO_USERS.find((user) => user.id === userId)!;

  useEffect(() => {
    let cancelled = false;
    setTokenState("loading");
    // 사용자가 바뀌는 동안 **이전 토큰으로 요청이 나가면 안 된다.** 먼저 지운다.
    setAccessToken(null);

    api
      .demoToken(userId)
      .then((issued) => {
        if (cancelled) return;
        setAccessToken(issued.token);
        setTokenState("ready");
      })
      .catch(() => {
        if (!cancelled) setTokenState("error");
      });

    return () => {
      cancelled = true;
    };
  }, [userId]);

  return (
    <div className="layout">
      <header className="topbar">
        <span className="brand">{TENANT.name} 아이템 거래소</span>

        <label className="row" style={{ gap: 6 }}>
          <span className="muted">데모 사용자</span>
          <select
            value={userId}
            onChange={(event) => setUserId(Number(event.target.value))}
          >
            {DEMO_USERS.map((user) => (
              <option key={user.id} value={user.id}>
                {user.username} ({user.role})
              </option>
            ))}
          </select>
        </label>

        <nav>
          <NavLink to="/" end>
            검색
          </NavLink>
          {currentUser.role === "ADMIN" && (
            <NavLink to="/anomalies">이상거래 큐</NavLink>
          )}
          <NotificationBadge />
        </nav>
      </header>

      {/* 토큰이 없는 동안 화면을 그리면 하위 쿼리가 전부 401로 실패한다.
          발급이 한 번의 왕복이라 스피너 대신 짧은 안내로 충분하다. */}
      {tokenState === "ready" ? (
        <Routes>
          <Route path="/" element={<Assistant />} />
          <Route path="/items/:itemId" element={<ItemDetail userId={userId} />} />
          <Route path="/anomalies" element={<AnomalyQueue />} />
        </Routes>
      ) : (
        <main className="card muted">
          {tokenState === "loading"
            ? "토큰 발급 중…"
            : "토큰 발급에 실패했습니다. 백엔드가 실행 중인지 확인하세요."}
        </main>
      )}
    </div>
  );
}

/**
 * 읽지 않은 알림 개수.
 *
 * 알림은 체결 후 **큐를 거쳐** 만들어지므로(ADR-0030) 구매 직후에는 아직 0일 수 있다.
 * 그래서 짧은 주기로 다시 물어본다 — 실패해도 화면을 깨뜨리지 않는다(부가 기능이다).
 *
 * 이 뱃지가 없으면 알림이 DB에만 쌓이고 **사람이 동작을 확인할 수 없다.** 자동 검증은
 * 백엔드 테스트와 load/verify-mq.sh 가 하고, 여기는 데모용 최소 노출이다.
 */
function NotificationBadge() {
  const { data } = useQuery({
    queryKey: ["notifications", "unread"],
    queryFn: api.unreadCount,
    refetchInterval: 5000,
    retry: false,
  });
  const count = data?.count ?? 0;
  if (count === 0) return null;
  return (
    <span className="badge unread" title="읽지 않은 알림">
      알림 {count}
    </span>
  );
}
