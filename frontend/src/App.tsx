import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";

import { api, setAccessToken, setSessionExpiredHandler, type LoginResult } from "./api";
import { TENANT } from "./demo";
import AnomalyQueue from "./pages/AnomalyQueue";
import Assistant from "./pages/Assistant";
import ItemDetail from "./pages/ItemDetail";
import Login from "./pages/Login";

export default function App() {
  // **드롭다운이 로그인 화면으로 바뀌었다** (ADR-0031). 예전에는 userId 를 고르면
  // demo-token 이 나왔는데, 비밀번호를 확인하지 않아 공개 배포에서는 성립하지 않는
  // 전제였다. 이제 서버가 자격증명을 검증한 뒤에 토큰을 준다.
  //
  // 세션은 여전히 메모리에만 둔다 — 새로고침하면 다시 로그인한다. localStorage 에
  // 넣으면 XSS 한 번에 토큰이 새므로, 데모 편의를 위해 그 위험을 지지 않는다.
  const [session, setSession] = useState<LoginResult | null>(null);

  function handleLogin(result: LoginResult) {
    setAccessToken(result.token);
    setSession(result);
  }

  function logout() {
    setAccessToken(null);
    setSession(null);
  }

  // **토큰 TTL 이 1시간이라 데모 탭을 열어두면 반드시 만나는 상태다** (ADR-0035).
  // 예전에는 만료 후 모든 동작이 에러를 내고 사용자가 직접 로그아웃을 눌러야 했다.
  // 자동 재발급은 불가능하다 — 비밀번호를 들고 있지 않기 때문이다(ADR-0031).
  // 할 수 있는 건 세션을 접고 로그인 화면으로 되돌리는 것뿐이고, 그게 맞다.
  useEffect(() => {
    setSessionExpiredHandler(() => setSession(null));
    return () => setSessionExpiredHandler(null);
  }, []);

  if (!session) {
    return <Login onSuccess={handleLogin} />;
  }

  const currentUser = session;

  return (
    <div className="layout">
      <header className="topbar">
        <span className="brand">{TENANT.name} 아이템 거래소</span>

        <span className="muted">
          {currentUser.username} ({currentUser.role})
        </span>
        <button type="button" className="linklike" onClick={logout}>
          로그아웃
        </button>

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

      <Routes>
        <Route path="/" element={<Assistant />} />
        <Route path="/items/:itemId" element={<ItemDetail userId={currentUser.userId} />} />
        <Route path="/anomalies" element={<AnomalyQueue />} />
      </Routes>
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
