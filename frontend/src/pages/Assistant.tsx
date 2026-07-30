import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { api, formatWon, type AssistantResponse } from "../api";

const EXAMPLES = [
  "3만원 이하 불속성 검 찾아줘",
  "불꽃의 대검 시세 알려줘",
  "불꽃의 대검 찾아서 시세도 알려줘",
  "거래 23659번 이상거래야?",
  "수수료 얼마인가요",
];

export default function Assistant() {
  const [query, setQuery] = useState("");
  const navigate = useNavigate();

  const ask = useMutation({
    mutationFn: (value: string) => api.ask(value),
  });

  function submit(value: string) {
    const trimmed = value.trim();
    if (!trimmed) return;
    setQuery(trimmed);
    ask.mutate(trimmed);
  }

  return (
    <div className="stack">
      <form
        className="row"
        onSubmit={(event) => {
          event.preventDefault();
          submit(query);
        }}
      >
        <input
          style={{ flex: 1, minWidth: 260 }}
          placeholder="무엇이든 물어보세요 — 검색·시세·이상거래를 알아서 나눠 처리합니다"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <button className="primary" type="submit" disabled={ask.isPending}>
          {ask.isPending ? "처리 중…" : "질문"}
        </button>
      </form>

      <div className="row">
        {EXAMPLES.map((example) => (
          <button key={example} type="button" onClick={() => submit(example)}>
            {example}
          </button>
        ))}
      </div>

      {ask.isPending && (
        <p className="muted">
          의도를 분류하고 필요한 도구를 부르는 중입니다. 복합 질의는 도구를 여러 번
          호출해서 20초 이상 걸릴 수 있습니다.
        </p>
      )}

      {ask.isError && (
        <p className="error">{(ask.error as Error).message}</p>
      )}

      {ask.data && <Result data={ask.data} onOpenItem={(id) => navigate(`/items/${id}`)} />}
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
