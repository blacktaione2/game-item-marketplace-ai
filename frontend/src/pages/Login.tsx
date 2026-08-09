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
 *
 * ## 이 화면은 소개가 아니라 문이다
 *
 * 한때 왼쪽 절반에 설계 요약 셋을 넣었다. 걷어낸 이유는 **중복**이다 — 같은 내용이
 * README 에 있고, 파이프라인이 실제로 어떻게 갈리는지는 로그인 뒤 화면이 배지와
 * 안내 블록으로 보여준다. 문 앞에서 두 번 말할 이유가 없다.
 *
 * ## 칩은 바로 로그인시킨다 — 다만 한 줄은 남긴다
 *
 * 칩을 누르면 채우고 곧바로 제출한다. 데모에서 아이디·비밀번호를 손으로 옮겨
 * 적게 할 이유가 없다.
 *
 * **그런데 그러면 겉모습이 ADR-0031 이 지운 그 드롭다운과 같아진다** — 이름을
 * 고르면 들어가지는 화면. 속은 정반대로 `POST /api/auth/login` 에 BCrypt 검증이지만,
 * 보는 사람은 구분할 수 없다. 그래서 **"실제 비밀번호 인증을 거친다"는 한 줄**을
 * 남긴다. 홍보 문구가 아니라 **화면이 자기가 무엇인지 잘못 말하는 것을 막는 문장**이다.
 *
 * 비밀번호 값 자체는 적지 않는다. 칩이 채워주므로 화면에서 쓸 일이 없고, 값이
 * 필요한 사람(직접 입력해 보려는 사람)은 README 에서 본다.
 */
export default function Login({ onSuccess }: { onSuccess: (r: LoginResult) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  /**
   * 폼 제출과 칩 클릭이 **같은 함수를 쓴다.** 칩 쪽에 따로 로그인 코드를 두면
   * 오류 처리나 busy 상태가 한쪽에만 붙는 일이 생긴다 — 이 저장소가 두 라우트의
   * 정산 규칙을 한 함수로 모은 것과 같은 이유다(ADR-0044).
   */
  async function doLogin(id: string, pw: string) {
    setBusy(true);
    setError(null);
    try {
      // 테넌트 코드는 상수다 (ADR-0034). 배포에 테넌트가 하나뿐이라 선택 UI 를 두지
      // 않았고, 둘 이상이 되면 여기가 선택기로 바뀌는 자리다.
      onSuccess(await api.login(TENANT.code, id, pw));
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
      <form
        className="card login-card"
        onSubmit={(event) => {
          event.preventDefault();
          void doLogin(username, password);
        }}
      >
        <h1>아이템 거래소</h1>
        <p className="muted">게임 아이템·계정·재화를 사고파는 거래소 데모입니다.</p>

        <label htmlFor="login-username">
          <span className="muted">아이디</span>
          {/* **`id`/`name` 이 없으면 비밀번호 관리자가 이 폼을 못 다룬다.**
              `autoComplete` 만으로는 부족해서 배포본 콘솔이 이슈로 잡고 있었다
              ("A form field element should have an id or name attribute"). */}
          <input
            id="login-username"
            name="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
            autoFocus
            required
          />
        </label>

        <label htmlFor="login-password">
          <span className="muted">비밀번호</span>
          <input
            id="login-password"
            name="password"
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

        <div className="demo-accounts">
          <span className="muted">데모 계정 — 누르면 바로 들어갑니다</span>
          <div className="row">
            {DEMO_LOGINS.map((account) => (
              <button
                key={account.username}
                type="button"
                // GM 은 테두리로도 구분한다 — 색만 쓰면 색각 이상과 흑백에서 사라진다.
                className={`chip${account.admin ? " warn" : ""}`}
                disabled={busy}
                onClick={() => {
                  const pw = account.admin ? ADMIN_PASSWORD : DEMO_PASSWORD;
                  // 입력칸에도 반영한다. 실패했을 때 무엇으로 시도했는지 보이고,
                  // 비밀번호 관리자도 이 값을 집어갈 수 있다.
                  setUsername(account.username);
                  setPassword(pw);
                  void doLogin(account.username, pw);
                }}
                title={account.note}
              >
                {account.username}
              </button>
            ))}
          </div>
          {/* **이 한 줄이 화면의 정직함을 지킨다.** 칩이 바로 들어가지므로 겉모습이
              ADR-0031 이 없앤 `demo-token` 드롭다운과 같아지는데, 그건 비밀번호를
              확인하지 않는 가짜 인증이었다. */}
          {/* 엔드포인트 경로는 안 적는다 — API 명세에 있고, 인라인 코드가 한국어
              조사와 붙으면 여백이 생겨 읽기가 끊긴다. 여기서 할 말은 "가짜가
              아니다" 한 가지다. */}
          <span className="muted">
            칩도 <strong>실제 비밀번호 인증</strong>(BCrypt)을 거칩니다. GM 계정으로
            들어가면 <strong>이상거래 검토 큐</strong>가 보입니다.
          </span>
        </div>
      </form>
    </div>
  );
}

/**
 * 공개된 데모 계정. **GM 도 공개한다** — 감추는 데 근거가 없었고, 감추면 큐 화면이
 * 모든 방문자에게 안 보였다. 경위는 ADR-0031 의 정정 블록.
 *
 * 두 비밀번호는 **서로 달라야 한다.** 같으면 역할 분리가 무의미해지고
 * `SecretGuard` 가 prod 기동을 거부한다. 값은 화면에 적지 않는다(README 에 있다).
 */
const DEMO_PASSWORD = "test1234";
const ADMIN_PASSWORD = "gmtest1234";

const DEMO_LOGINS = [
  { username: "buyer_lee", note: "구매·입찰 이력이 있는 계정", admin: false },
  { username: "seller_kim", note: "판매자 쪽 거래가 섞여 있다", admin: false },
  { username: "trader_park", note: "", admin: false },
  { username: "newbie_choi", note: "", admin: false },
  { username: "gm_admin", note: "GM — 이상거래 검토 큐를 볼 수 있다", admin: true },
];
