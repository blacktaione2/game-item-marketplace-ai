import { useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";

import { DEFAULT_USER_ID, DEMO_USERS, TENANT } from "./demo";
import AnomalyQueue from "./pages/AnomalyQueue";
import Assistant from "./pages/Assistant";
import ItemDetail from "./pages/ItemDetail";

export default function App() {
  // 인증이 없으므로 "로그인한 사용자"를 드롭다운으로 고른다. 선택한 id가
  // 그대로 X-User-Id 헤더로 나간다 — 백엔드가 기대하는 임시 식별 방식이다.
  const [userId, setUserId] = useState(DEFAULT_USER_ID);
  const currentUser = DEMO_USERS.find((user) => user.id === userId)!;

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
        </nav>
      </header>

      <Routes>
        <Route path="/" element={<Assistant />} />
        <Route path="/items/:itemId" element={<ItemDetail userId={userId} />} />
        <Route path="/anomalies" element={<AnomalyQueue />} />
      </Routes>
    </div>
  );
}
