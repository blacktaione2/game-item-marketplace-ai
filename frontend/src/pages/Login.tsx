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
 * ## 왜 왼쪽 절반이 생겼나
 *
 * 이 화면은 **처음 온 사람이 3초 안에 "이게 뭔지" 판단하는 유일한 자리**인데,
 * 검은 배경 한가운데 입력칸 두 개가 전부였다. 실제 배포 화면을 브라우저로 열어
 * 확인한 결과이기도 하다. 로그인 폼만 있는 화면은 **자기가 무엇의 문인지 말하지
 * 않는다.**
 *
 * 왼쪽에 적은 셋은 홍보 문구가 아니라 이 저장소가 **측정해서 근거를 가진 것**들만
 * 골랐다(요청 타입별 분기 · 하이브리드 검색 + 재순위 · 오버셀 0건).
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
      <div className="login-grid">
        <section className="login-pitch">
          <h1>아이템 거래소</h1>
          <p className="lede">
            게임 아이템·계정·재화를 사고파는 거래소입니다. 자연어 요청을
            <strong> 종류별로 다른 파이프라인</strong>에 태워 처리합니다.
          </p>

          <ul className="pitch-list">
            <li>
              <strong>요청마다 다르게 씁니다</strong>
              <span className="muted">
                검색 · 시세 예측 · 이상거래 점검 · 복합 질의를 라우터가 먼저 가릅니다.
                안내성 질문은 LLM을 아예 부르지 않습니다
              </span>
            </li>
            <li>
              <strong>검색은 BM25 + 벡터 하이브리드</strong>
              <span className="muted">
                RRF로 융합한 뒤 크로스 인코더로 재순위하고, 종류·속성은 임계값이
                아니라 하드 필터로 거릅니다
              </span>
            </li>
            <li>
              <strong>동시 구매 오버셀 0건</strong>
              <span className="muted">
                Redis 분산 락 + 낙관적 락. 약 26,600건 동시 구매 부하로 검증했습니다
              </span>
            </li>
          </ul>

          <div className="demo-accounts">
            <span className="muted">데모 계정 — 눌러서 채우세요</span>
            <div className="row">
              {DEMO_LOGINS.map((account) => (
                <button
                  key={account.username}
                  type="button"
                  className="chip"
                  onClick={() => {
                    setUsername(account.username);
                    setPassword(DEMO_PASSWORD);
                  }}
                  title={account.note}
                >
                  {account.username}
                </button>
              ))}
            </div>
            {/* **괄호와 `<code>` 사이에 공백을 만들지 않는다.** JSX 는 줄바꿈을
                공백으로 바꾸므로, 태그를 다음 줄로 내리면 화면에 `( gm_admin )`
                처럼 벌어져 보인다. */}
            <span className="muted">
              비밀번호 <code>{DEMO_PASSWORD}</code> · GM 계정(<code>gm_admin</code>)은
              이상거래 큐를 볼 수 있어 비밀번호를 공개하지 않습니다
            </span>
          </div>
        </section>

        <form className="card login-card" onSubmit={submit}>
          <h2>로그인</h2>

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
        </form>
      </div>
    </div>
  );
}

/**
 * 공개된 데모 계정. **`gm_admin` 은 일부러 뺐다** — 이상거래 큐에 닿을 수 있는
 * 유일한 역할이라 비밀번호가 공개 대상이 아니다(README 와 같은 기준).
 *
 * 비밀번호를 화면에 적는 것이 모순이 아닌 이유는 README 에 적혀 있다: 이 로그인
 * 게이트의 목적은 비밀을 지키는 게 아니라 **크롤러와 우연한 접근을 막고 요청
 * 한도를 신원에 걸기 위한 것**이다.
 */
const DEMO_PASSWORD = "test1234";

const DEMO_LOGINS = [
  { username: "buyer_lee", note: "구매·입찰 이력이 있는 계정" },
  { username: "seller_kim", note: "판매자 쪽 거래가 섞여 있다" },
  { username: "trader_park", note: "" },
  { username: "newbie_choi", note: "" },
];
