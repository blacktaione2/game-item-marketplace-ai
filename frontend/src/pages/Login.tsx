import { useState } from "react";

import { api, type LoginResult } from "../api";
import { TENANT } from "../demo";

/**
 * 로그인 (ADR-0031).
 *
 * 예전에는 데모 사용자 드롭다운이 `demo-token` 을 받아왔다 — 비밀번호를 확인하지
 * 않아 userId 만 알면 누구나 그 사용자가 될 수 있었고, 공개 배포에서는 성립하지
 * 않는 전제였다.
 *
 * **회원가입 링크가 없는 것은 누락이 아니다.** 계정은 시드로 고정이고, 가입을 열면
 * 이메일 인증·비밀번호 재설정에 더해 공개 배포에서는 스팸 계정 방어까지 따라온다.
 */
export default function Login({ onSuccess }: { onSuccess: (r: LoginResult) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      // 테넌트 코드는 상수다 (ADR-0034). 배포에 테넌트가 하나뿐이라 선택 UI 를 두지
      // 않았고, 둘 이상이 되면 여기가 선택기로 바뀌는 자리다.
      onSuccess(await api.login(TENANT.code, username, password));
    } catch {
      // 서버가 아이디와 비밀번호를 구분하지 않으므로 화면도 구분하지 않는다 —
      // 여기서 갈라 말하면 서버가 막아둔 사용자 열거가 화면에서 열린다.
      setError("아이디 또는 비밀번호가 올바르지 않습니다.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-wrap">
      <form className="card login-card" onSubmit={submit}>
        <h1>아이템 거래소</h1>
        <p className="muted">데모 계정으로 로그인하세요.</p>

        <label>
          <span className="muted">아이디</span>
          <input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
            autoFocus
            required
          />
        </label>

        <label>
          <span className="muted">비밀번호</span>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            required
          />
        </label>

        {error && <p className="login-error">{error}</p>}

        <button type="submit" disabled={busy || !username || !password}>
          {busy ? "확인 중…" : "로그인"}
        </button>
      </form>
    </div>
  );
}
