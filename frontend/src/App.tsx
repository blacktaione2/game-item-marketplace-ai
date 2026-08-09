import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { NavLink, Route, Routes, useNavigate } from "react-router-dom";

import { api, setAccessToken, setSessionExpiredHandler, type LoginResult } from "./api";
import AnomalyQueue from "./pages/AnomalyQueue";
import Assistant from "./pages/Assistant";
import ItemDetail from "./pages/ItemDetail";
import Login from "./pages/Login";
import Notifications from "./pages/Notifications";
import Trades from "./pages/Trades";

/**
 * 세션 보관 (ADR-0036).
 *
 * **`sessionStorage`다. `localStorage`가 아니다.** ADR-0031은 세션을 메모리에만
 * 뒀는데, 그러면 새로고침 한 번에 로그아웃이라 데모로 못 쓴다 — 실제로 상세
 * 화면에서 F5를 눌러 걸렸다.
 *
 * 메모리 → sessionStorage 로 옮기면서 잃는 게 크지 않다는 게 판단의 근거다.
 * XSS가 나면 **메모리에 있는 토큰도 그 자리에서 빼간다** — 공격자가 이미 페이지
 * 안에서 JS를 돌리고 있기 때문이다. 실질적 차이는 `localStorage`와의 사이에
 * 있다: 탭을 닫아도 살아남는가, 탭 사이에 공유되는가. sessionStorage는 둘 다
 * 아니다.
 */
const SESSION_KEY = "gimp.session";

function loadSession(): LoginResult | null {
  try {
    const raw = sessionStorage.getItem(SESSION_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as LoginResult;
    // 렌더보다 먼저 토큰을 꽂아야 첫 요청이 401을 맞지 않는다.
    setAccessToken(parsed.token);
    return parsed;
  } catch {
    // 손상된 값 하나 때문에 앱이 안 뜨면 안 된다. 지우고 로그인 화면으로 간다.
    sessionStorage.removeItem(SESSION_KEY);
    return null;
  }
}

// 모듈 로드 시점에 한 번. `useState` 초기화 함수에 두면 StrictMode가 두 번
// 부르고, 무엇보다 렌더 중에 부수효과를 내게 된다.
const restoredSession = loadSession();

export default function App() {
  // **드롭다운이 로그인 화면으로 바뀌었다** (ADR-0031). 예전에는 userId 를 고르면
  // demo-token 이 나왔는데, 비밀번호를 확인하지 않아 공개 배포에서는 성립하지 않는
  // 전제였다. 이제 서버가 자격증명을 검증한 뒤에 토큰을 준다.
  const [session, setSession] = useState<LoginResult | null>(restoredSession);
  const queryClient = useQueryClient();
  const navigate = useNavigate();

  function handleLogin(result: LoginResult) {
    setAccessToken(result.token);
    sessionStorage.setItem(SESSION_KEY, JSON.stringify(result));
    setSession(result);
  }

  /**
   * 로그아웃.
   *
   * **`queryClient.clear()` 가 이 함수의 핵심이다.** 토큰만 지우면 이전 계정의
   * 응답이 캐시에 그대로 남아 다음 로그인 화면에 잠깐 보인다 — 쿼리 키에 사용자가
   * 안 들어 있기 때문이다(`["notifications","unread"]`, `["trades"]`, `["alerts"]`).
   * 재요청이 오기 전까지 **남의 데이터가 내 화면에** 있는 셈이고, 이건 편의 문제가
   * 아니라 격리 문제다.
   *
   * 키마다 사용자 id 를 넣는 대안도 있지만, 그러면 새 쿼리를 추가할 때마다 잊을 수
   * 있다. 세션이 끝나면 서버 상태는 전부 무효라는 게 더 단순하고 빠뜨릴 데가 없다.
   *
   * `navigate("/")` 는 재로그인 후 직전 화면(`/items/5` 등)에 떨어지지 않게 한다.
   */
  function logout() {
    setAccessToken(null);
    sessionStorage.removeItem(SESSION_KEY);
    queryClient.clear();
    navigate("/");
    setSession(null);
  }

  // **토큰 TTL 이 1시간이라 데모 탭을 열어두면 반드시 만나는 상태다** (ADR-0035).
  // 예전에는 만료 후 모든 동작이 에러를 내고 사용자가 직접 로그아웃을 눌러야 했다.
  // 자동 재발급은 불가능하다 — 비밀번호를 들고 있지 않기 때문이다(ADR-0031).
  // 할 수 있는 건 세션을 접고 로그인 화면으로 되돌리는 것뿐이고, 그게 맞다.
  //
  // **저장소도 같이 비워야 한다** (ADR-0036). 상태만 지우면 만료된 토큰이
  // sessionStorage 에 남아, 새로고침할 때마다 복원 → 첫 요청 401 → 로그인 화면이
  // 반복된다. 세션을 끝내는 자리는 세 곳(로그아웃·만료·복원 실패) 전부 같은 일을
  // 해야 한다.
  useEffect(() => {
    setSessionExpiredHandler(() => {
      sessionStorage.removeItem(SESSION_KEY);
      setSession(null);
    });
    return () => setSessionExpiredHandler(null);
  }, []);

  if (!session) {
    return <Login onSuccess={handleLogin} />;
  }

  const currentUser = session;

  return (
    <div className="layout">
      <header className="topbar">
        <span className="brand">아이템 거래소</span>

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
          <NavLink to="/trades">거래 내역</NavLink>
          {currentUser.role === "ADMIN" && (
            <NavLink to="/anomalies">이상거래 큐</NavLink>
          )}
          <NotificationBadge />
        </nav>
      </header>

      <Routes>
        <Route path="/" element={<Assistant />} />
        <Route path="/items/:itemId" element={<ItemDetail userId={currentUser.userId} />} />
        <Route path="/trades" element={<Trades />} />
        <Route path="/notifications" element={<Notifications />} />
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

  // **개수가 0이어도 링크는 남긴다.** 예전엔 통째로 사라져서 지난 알림을 볼
  // 방법이 없었다 — 배지가 숫자를 나르는 동시에 유일한 진입점이라, 숨기면
  // 화면 하나가 같이 사라진다.
  //
  // 숫자를 **링크 안의 span** 으로 둔다. 링크 자체에 `badge` 를 걸면
  // `.topbar nav a` 와 `.badge` 가 padding·radius 를 두고 특이도로 다투는데,
  // 어느 쪽이 이기는지는 규칙을 세어봐야 안다. 중첩하면 셀 필요가 없다.
  return (
    <NavLink to="/notifications">
      알림
      {count > 0 && (
        <span className="badge unread" style={{ marginLeft: 6 }}>
          {count}
        </span>
      )}
    </NavLink>
  );
}
