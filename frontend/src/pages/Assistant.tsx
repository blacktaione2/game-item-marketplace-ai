import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { api, formatWon, type AssistantResponse } from "../api";
import ItemBrowser from "../components/ItemBrowser";

/**
 * 네 분기를 한 번씩 밟는 예시. **버튼이 곧 질의**이므로 문구를 짧게 유지한다 —
 * 길면 칩이 두 줄로 접힌다.
 *
 * 첫 항목은 원래 `"3만원 이하 불속성 검 찾아줘"` 였다. ADR-0016 의 0건 처리를
 * 시연하는 질의인데(화염 속성 검 중 3만원 이하가 없다), **첫인상이 빈 결과**라
 * 결과가 나오는 질의로 바꿨다. 0건 경로는 그 문장을 직접 입력하면 그대로 나온다.
 */
const EXAMPLES = [
  "5만원 이하 검 찾아줘",
  "불꽃의 대검 시세 알려줘",
  "불꽃의 대검 찾아서 시세도 알려줘",
  "거래 23659번 이상거래야?",
  "수수료 얼마인가요",
];

/**
 * 질의를 **URL 에 둔다** (`/?q=...`).
 *
 * 예전에는 `useState` + `useMutation` 이었다. 그러면 아이템 상세로 들어갔다
 * 돌아올 때 이 컴포넌트가 재마운트되면서 **검색 결과가 통째로 사라진다** —
 * 지역 상태는 언마운트와 함께 없어지고, mutation 결과는 캐시에 안 남는다.
 * "← 검색으로" 를 뒤로가기로 바꿔도 그 자체로는 해결되지 않는 문제였다.
 *
 * URL 에 있으면 세 가지가 한꺼번에 따라온다: 뒤로가기가 질의를 되살리고,
 * `useQuery` 가 그 질의를 키로 캐시된 응답을 즉시 내주고, 검색 링크를 공유할
 * 수 있다.
 */
export default function Assistant() {
  const [searchParams, setSearchParams] = useSearchParams();
  const submitted = searchParams.get("q") ?? "";
  const [draft, setDraft] = useState(submitted);
  const navigate = useNavigate();

  // 뒤로/앞으로로 URL 이 바뀌면 입력창도 따라가야 한다. 안 그러면 결과는
  // 이전 질의인데 입력창은 다른 글자가 남는다.
  useEffect(() => setDraft(submitted), [submitted]);

  const ask = useQuery({
    queryKey: ["assistant", submitted],
    queryFn: () => api.ask(submitted),
    enabled: submitted.length > 0,
    // **`staleTime` 을 두지 않는다.** 처음엔 5분을 걸었다 — "AI 응답은 비싸니
    // 돌아왔을 때 다시 부르지 말자". 그게 **배지를 거짓말하게 만들었다**: 같은
    // 질의를 다시 물으면 서버를 안 부르고 첫 응답을 다시 그리는데, 거기엔
    // `cache: {hit:false}` 가 박혀 있어 **영원히 미적중으로 보인다.**
    //
    // 이 화면의 배지 줄은 서버 파이프라인을 눈에 보이게 하는 장치다(캐시 적중,
    // LLM 호출 수, 라우팅 판정). 그 값을 클라이언트가 얼려두면 계측이 아니라
    // 잔상이 된다.
    //
    // 비용 걱정은 서버 캐시가 이미 답한다 — 적중 경로는 LLM 0회에 p95 25.9ms
    // 다(ADR-0026). 그리고 기본값(`staleTime: 0`)이어도 **캐시된 데이터는 즉시
    // 그려지고 뒤에서 다시 받는다** — 뒤로가기로 결과가 살아 돌아오는 동작은
    // 그대로다.
    retry: false,
  });

  function submit(value: string) {
    const trimmed = value.trim();
    if (!trimmed) return;
    if (trimmed === submitted) {
      // **같은 질의면 URL 이 안 바뀌고, 그러면 아무 일도 안 일어난다.**
      // 처음엔 "캐시된 결과가 이미 떠 있으니 맞는 동작"이라고 뒀는데 틀렸다 —
      // 버튼을 눌렀는데 반응이 없으면 사용자에게는 고장이다. 다시 물은 것은
      // 다시 묻겠다는 뜻이므로 다시 부른다(서버 캐시가 대개 0회로 받아준다).
      void ask.refetch();
      return;
    }
    setSearchParams({ q: trimmed });
  }

  return (
    <div className="stack">
      <form
        className="row"
        onSubmit={(event) => {
          event.preventDefault();
          submit(draft);
        }}
      >
        <input
          style={{ flex: 1, minWidth: 260 }}
          placeholder="무엇이든 물어보세요 — 검색·시세·이상거래를 알아서 나눠 처리합니다"
          value={draft}
          // 서버 상한과 같은 값이다 (ADR-0035). **서버가 막으니 됐다고 두지 않는다** —
          // 붙여넣기로 500자를 넘기면 422 가 오고, 사용자에게는 원인이 안 보이는
          // 오류로만 보인다. 여기서 막으면 애초에 그 상태가 안 생긴다.
          // 서버 검증은 그대로 남는다(프론트를 거치지 않는 호출이 있다).
          maxLength={500}
          onChange={(event) => setDraft(event.target.value)}
        />
        {/* **`isFetching` 이지 `isPending` 이 아니다.** v5 에서 `enabled:false` 인
            쿼리는 status 가 계속 `pending` 이라, `isPending` 을 쓰면 질의를 넣기
            전부터 버튼이 "처리 중…" 으로 잠긴다. 실제로 도는 중인지는
            `isFetching` 만 안다. */}
        <button className="primary" type="submit" disabled={ask.isFetching}>
          {ask.isFetching ? "처리 중…" : "질문"}
        </button>
      </form>

      <div className="row chip-row">
        {EXAMPLES.map((example) => (
          <button
            key={example}
            type="button"
            className="chip"
            onClick={() => submit(example)}
          >
            {example}
          </button>
        ))}
      </div>

      {ask.isFetching && (
        <p className="muted">
          의도를 분류하고 필요한 도구를 부르는 중입니다. 복합 질의는 도구를 여러 번
          호출해서 20초 이상 걸릴 수 있습니다.
        </p>
      )}

      {ask.isError && (
        <p className="error">{(ask.error as Error).message}</p>
      )}

      {ask.data && <Result data={ask.data} onOpenItem={(id) => navigate(`/items/${id}`)} />}

      {/* **질의가 없을 때의 화면이 빈 여백이면 안 된다.** 거래소인데 매물을 볼 수가
          없었고, 처음 온 사람은 무엇을 물어야 하는지도 몰랐다. 검색의 "빈 상태"를
          목록으로 채운다 — 별도 라우트를 만들지 않은 이유는 이 둘이 같은 질문의
          두 형태이기 때문이다. */}
      {!submitted && <ItemBrowser />}
    </div>
  );
}

function Result({
  data,
  onOpenItem,
}: {
  data: AssistantResponse;
  onOpenItem: (itemId: number) => void;
}) {
  const escalated = data.routing.initial_intent !== data.intent;

  return (
    <div className="stack">
      {/* 이 배지들이 Phase 6 파이프라인을 눈에 보이게 만드는 지점이다.
          특히 캐시 히트인데 llm_calls가 0이 아니면 회귀다. */}
      <div className="row">
        <span className="badge">
          의도 <strong>{data.intent}</strong>
        </span>
        <span className="badge">
          판정 <strong>{data.routing.decided_by}</strong>
          {data.routing.confidence != null && ` · ${data.routing.confidence}`}
        </span>
        {escalated && (
          <span className="badge warn">
            에스컬레이션 <strong>{data.routing.initial_intent} → {data.intent}</strong>
          </span>
        )}
        <span className={`badge ${data.cache.hit ? "hit" : ""}`}>
          캐시{" "}
          <strong>
            {data.cache.hit ? `적중 (${data.cache.match_type})` : "미적중"}
          </strong>
        </span>
        <span className={`badge ${data.cache.hit && data.llm_calls > 0 ? "fail" : ""}`}>
          LLM 호출 <strong>{data.llm_calls}회</strong>
        </span>
        {/* 0건은 LLM을 안 거친 확정 응답이다. 설명 생성 호출이 빠져서 2회가
            아니라 1회여야 한다 — 2회면 회귀다. */}
        {data.no_results && (
          <span className={`badge ${data.llm_calls > 1 ? "fail" : "hit"}`}>
            결과 없음 <strong>LLM 설명 생략</strong>
          </span>
        )}
        {data.tool_failures ? (
          <span className="badge fail">
            도구 실패 <strong>{data.tool_failures}건</strong>
          </span>
        ) : null}
      </div>

      {data.cache.hit && data.cache.cached_query !== data.query && (
        <p className="muted">
          유사 질의 “{data.cache.cached_query}”의 캐시를 재사용했습니다
          (유사도 {data.cache.similarity}).
        </p>
      )}

      <div className="card answer">{data.answer}</div>

      {/* 결과가 있으면 항목 자체가 종류·속성을 달고 나와서 검증이 되는데,
          0건에는 검증할 대상이 없다. 그래서 걸린 조건을 그대로 보여준다 —
          질의를 잘못 해석한 것과 진짜 없는 것을 구분할 유일한 수단이다. */}
      {data.no_results && data.conditions && data.conditions.length > 0 && (
        <div className="card">
          <div className="muted" style={{ marginBottom: 8 }}>
            적용된 검색 조건
          </div>
          <div className="row">
            {data.conditions.map((condition) => (
              <span key={condition} className="badge">
                {condition}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* **오토인코더를 고른 이유가 여기서만 보인다.** ADR-0009 가 Isolation
          Forest 대신 이걸 택한 근거는 재구성 오차의 피처별 분해뿐인데, 그 화면이
          GM 전용 검토 큐에만 있었다. 판정 API 는 일반 사용자도 쓸 수 있고
          (`require_actor`), 기여도는 이 응답에 **이미 실려 오고 있었다** — 화면이
          안 그렸을 뿐이다. LLM 이 쓴 문장 옆에 근거를 같이 둔다. */}
      {data.detection && (
        <div className="card">
          <div className="row" style={{ marginBottom: 10 }}>
            <span className={`badge ${data.detection.is_anomaly ? "fail" : "hit"}`}>
              {data.detection.is_anomaly ? "이상 판정" : "정상 범위"}
            </span>
            <span className="badge">
              이상 점수 <strong>{data.detection.anomaly_score.toFixed(1)}</strong>
              <span className="muted"> / 임계 {data.detection.threshold.toFixed(1)}</span>
            </span>
            <span className="badge">
              시세 대비 <strong>{data.detection.price_ratio.toFixed(2)}배</strong>
            </span>
          </div>
          <div className="muted" style={{ marginBottom: 6 }}>
            이상 판정 기여도
          </div>
          {data.detection.contributions.map((contribution) => (
            <div className="contrib" key={contribution.feature}>
              <span>{contribution.feature}</span>
              <div className="contrib-track">
                <div
                  className="contrib-fill"
                  style={{ width: `${Math.max(contribution.share * 100, 1)}%` }}
                />
              </div>
              <span style={{ textAlign: "right" }}>
                {Math.round(contribution.share * 100)}%
              </span>
            </div>
          ))}
          {/* 합성 코퍼스 id 라는 사실은 여기서도 밝힌다 — 사용자가 이 번호를
              자기 거래 조회에 넣지 않게 하는 게 목적이라 화면마다 필요하다. */}
          <div className="muted" style={{ marginTop: 10, fontSize: 13 }}>
            거래 <strong>합성#{data.detection.trade_id}</strong> · 이 번호는 AI 데모
            데이터의 id 이며 실제 거래 번호와 다릅니다.
          </div>
        </div>
      )}

      {data.tool_calls && data.tool_calls.length > 0 && (
        <div className="card">
          <div className="muted" style={{ marginBottom: 8 }}>
            에이전트 도구 호출
          </div>
          <table>
            <thead>
              <tr>
                <th>단계</th>
                <th>도구</th>
                <th>인자</th>
                <th>결과</th>
              </tr>
            </thead>
            <tbody>
              {data.tool_calls.map((call, index) => (
                <tr key={index}>
                  <td>{call.step}</td>
                  <td>{call.tool}</td>
                  <td className="muted">{JSON.stringify(call.arguments)}</td>
                  <td style={{ color: call.failed ? "var(--critical)" : undefined }}>
                    {call.failed ? "실패" : "성공"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {data.results && data.results.length > 0 && (
        <div className="item-grid">
          {data.results.map((item) => (
            <div
              key={item.item_id}
              className="item-card"
              onClick={() => onOpenItem(item.item_id)}
            >
              <h4>{item.name}</h4>
              <div className="price">{formatWon(item.price)}</div>
              <div className="muted">
                {item.subcategory ?? item.category}
                {/* 무속성은 안 띄운다 — 42건 중 35건이라 전부 띄우면 노이즈다.
                    +0 강화나 0렙을 안 띄우는 것과 같은 규칙. */}
                {item.element && item.element !== "무속성" && ` · ${item.element}`}
                {item.enhancement_level > 0 && ` · +${item.enhancement_level}`}
                {item.required_level > 0 && ` · ${item.required_level}렙`}
                {item.sale_type === "AUCTION" && " · 경매"}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
