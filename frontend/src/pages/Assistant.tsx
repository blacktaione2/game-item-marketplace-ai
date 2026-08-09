import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import {
  askStream,
  formatWon,
  type AssistantResponse,
  type ProgressEvent,
  type SearchResultItem,
} from "../api";
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

  // 진행 단계. **응답과 함께 캐시하지 않는다** — 이건 "지금 무슨 일이
  // 일어나고 있는가"라서, 뒤로가기로 돌아왔을 때 되살아나면 거짓말이 된다.
  // `staleTime` 을 안 두는 이유(아래)와 같은 계열이다.
  const [progress, setProgress] = useState<ProgressEvent[]>([]);

  const ask = useQuery({
    queryKey: ["assistant", submitted],
    // **`signal` 을 실제로 넘긴다.** `askStream` 은 처음부터 이 옵션을 받고
    // 있었는데 **아무도 안 넘겼다.** 그래서 서버의 "클라이언트가 끊으면 하던
    // 일을 취소한다" 는 방어가 이 앱에서는 한 번도 실행되지 않았다 —
    // *걷지 않은 경로는 동작한다고 말할 수 없다.*
    //
    // TanStack Query 가 이 신호를 관리한다: 관찰자가 사라지거나(화면 이동)
    // 재요청이 나가면 이전 스트림이 중단된다. 없으면 아이템 상세로 넘어가도
    // 스트림이 살아남아 **아무도 안 읽는 답변에 LLM 요금이 계속 나간다.**
    queryFn: ({ signal }) => {
      setProgress([]);
      return askStream(
        submitted,
        (event) => {
          if (event.type === "progress") {
            setProgress((prev) => [...prev, event]);
          }
        },
        { signal },
      );
    },
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
          // `id`/`name` 이 없으면 브라우저가 이 필드를 다루지 못한다(자동완성·
          // 기록). 배포본 콘솔이 이슈로 잡고 있던 셋 중 하나다.
          id="assistant-query"
          name="q"
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

      {ask.isFetching && <Progress events={progress} />}

      {ask.isError && (
        <p className="error">{(ask.error as Error).message}</p>
      )}

      {ask.data && <Result data={ask.data} onOpenItem={(id) => navigate(`/items/${id}`)} />}

      {/* 질의 전에는 **무엇을 물을 수 있는지**를 설명이 대신한다. 배지가 실제
          응답으로 채워지기 전까지 이 프로젝트의 요점(요청마다 다른 경로)이
          화면 어디에도 안 보였다. */}
      {!submitted && !ask.isFetching && <PipelineHint />}

      {/* **질의가 없을 때의 화면이 빈 여백이면 안 된다.** 거래소인데 매물을 볼 수가
          없었고, 처음 온 사람은 무엇을 물어야 하는지도 몰랐다. 검색의 "빈 상태"를
          목록으로 채운다 — 별도 라우트를 만들지 않은 이유는 이 둘이 같은 질문의
          두 형태이기 때문이다.

          **검색 뒤에도 남긴다.** 예전에는 `!submitted` 조건이라 검색이 성공하면
          표가 통째로 사라졌다 — 배포 화면을 열어보니 결과 4건 아래로 뷰포트의
          40%가 빈칸이었고, **가장 몰입한 순간에 화면이 더 비는** 모양이었다.
          결과가 있을 때는 제목을 붙여 두 목록을 구분한다. */}
      {submitted && ask.data && <hr className="sep" />}
      <ItemBrowser heading={submitted ? "전체 매물에서 더 보기" : undefined} />
    </div>
  );
}

/**
 * 질의 전 안내 — **이 프로젝트가 무엇을 다르게 하는지**를 화면에 남긴다.
 *
 * 응답의 배지 줄(의도·판정·캐시·LLM 호출 수)이 이 서비스의 요점인데, 그건
 * **질문을 해야만 나타난다.** 처음 온 사람에게는 검색창 하나만 보이고, 그러면
 * 평범한 검색창과 구분되지 않는다.
 *
 * 숫자는 `docs/02-AI-Pipeline/요청-타입별-파이프라인.md` 의 값과 같아야 한다.
 * 복합만 실측 범위(ADR-0046)라 고정 숫자를 안 쓴다.
 */
function PipelineHint() {
  return (
    <div className="card stack hint">
      <div className="row">
        <strong>요청을 종류별로 나눠 처리합니다</strong>
        <span className="muted">— 답이 나올 때 실제 경로가 배지로 표시됩니다</span>
      </div>
      <div className="row hint-grid">
        {[
          ["아이템 검색", "BM25 + 벡터 → RRF → 재순위", "LLM 2회"],
          ["시세 예측", "LSTM · 이력이 얕으면 Cold Start", "LLM 3회"],
          ["이상거래 점검", "오토인코더 + 피처별 기여도", "LLM 1회"],
          ["안내·인사", "정적 응답", "LLM 0회"],
          ["복합 질의", "MCP 도구를 여러 번 호출", "LLM 실측"],
        ].map(([title, how, cost]) => (
          <div key={title} className="hint-cell">
            <strong>{title}</strong>
            <span className="muted">{how}</span>
            <span className="badge">{cost}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/** 의도 코드를 사람 말로. 서버는 사실만 주고 문구는 화면이 정한다. */
const INTENT_LABEL: Record<string, string> = {
  item_search: "아이템 검색",
  price_forecast: "시세 예측",
  anomaly_check: "이상거래 점검",
  faq_smalltalk: "안내",
  compound: "복합 질의 — 도구를 여러 번 부릅니다",
};

/** MCP 도구 이름을 사람 말로. */
const TOOL_LABEL: Record<string, string> = {
  search_items: "아이템 검색",
  forecast_item_price: "시세 조회",
  check_trade_anomaly: "거래 점검",
};

function describe(event: ProgressEvent): string {
  switch (event.stage) {
    case "cache":
      return event.hit ? "이전 답변을 찾았습니다" : "캐시 확인";
    case "routing":
      return `의도 판별: ${INTENT_LABEL[event.intent ?? ""] ?? event.intent}`;
    case "branch":
      return "처리 중";
    case "thinking":
      // **이 줄이 가장 긴 대기를 덮는다.** 실측하면 여기서 6~7초가 지나간다 —
      // 다음에 어떤 도구를 부를지 모델이 정하는 시간이다.
      return event.step && event.step > 1
        ? `다음 단계를 정하는 중 (${event.step})`
        : "필요한 도구를 정하는 중";
    case "tool":
      // **`tool` 은 호출이 끝난 게 아니라 시작한 것**이다. 도구 실행이 10초를
      // 넘기도 해서, 끝날 때만 알리면 그동안 "도구를 정하는 중"이라는 틀린
      // 라벨이 떠 있었다. `failed` 는 실패했을 때만 한 번 더 온다.
      return event.failed
        ? `${TOOL_LABEL[event.tool ?? ""] ?? event.tool} — 실패`
        : `${TOOL_LABEL[event.tool ?? ""] ?? event.tool} 중`;
  }
}

/**
 * 진행 상황.
 *
 * **토큰이 아니라 단계를 흘리기로 한 이유가 여기서 보인다.** 복합 질의의
 * 7~25초는 대부분 도구 호출이라, 사용자가 기다리는 동안 알고 싶은 것은 글자가
 * 나오기 시작했는지가 아니라 **지금 뭘 하고 있는가**다.
 *
 * 이벤트가 아직 없을 때도 문구를 낸다 — 스트림이 열리기까지의 짧은 순간에
 * 화면이 비면 눌리지 않은 것처럼 보인다.
 */
function Progress({ events }: { events: ProgressEvent[] }) {
  if (events.length === 0) {
    return <p className="muted">요청을 보내는 중…</p>;
  }
  return (
    <ul className="progress">
      {events.map((event, index) => (
        <li
          key={index}
          className={
            // 마지막 줄만 "진행 중"이고 나머지는 끝난 단계다.
            index === events.length - 1 ? "progress-now" : "progress-done"
          }
        >
          {describe(event)}
        </li>
      ))}
    </ul>
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
      {/* **답변이 먼저 온다.** 예전엔 의도·캐시·LLM 호출 배지가 답변 위에
          있었는데, 그러면 내부 계측이 제품보다 먼저 읽힌다 — 실제 거래소라면
          사용자에게 `intent` 를 보여주지 않는다.

          그렇다고 지우지도 않는다. 이 프로젝트의 주장("LLM API 를 감싸기만 한
          서비스가 되지 않는 것")을 화면에서 확인할 수 있는 **유일한 지점**이라
          없애면 링크를 여는 사람에게는 챗봇 하나로 보인다.

          그래서 아래로 내리고 라벨을 붙였다. 위치와 이름이 "실수로 새어나온
          내부"와 "의도적인 관측 패널"을 가른다. */}
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

      {/* **시세·복합 답변에는 결과 그리드가 없다.** 검색은 항목이 카드로 나와서
          클릭해 들어갈 수 있는데, 저 둘은 문장뿐이라 "어느 아이템 얘기인지"
          확인하러 갈 방법이 없었다.

          **검색 결과와 같은 `ItemCard` 를 쓴다.** 처음엔 이름만 담은 칩으로
          만들었는데, 같은 아이템이 화면마다 다른 모양으로 보였다. 서버가
          `resolved_item` 을 검색 항목과 같은 형태로 주게 맞춘 이유가 이것이다. */}
      {data.resolved_item && (
        <div className="stack">
          <div className="muted">이 답변이 가리키는 아이템</div>
          <div className="item-grid">
            <ItemCard item={data.resolved_item} onOpen={onOpenItem} />
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

      {data.results && data.results.length > 0 && (
        <div className="item-grid">
          {data.results.map((item) => (
            <ItemCard key={item.item_id} item={item} onOpen={onOpenItem} />
          ))}
        </div>
      )}

      {/* --- 여기부터는 제품이 아니라 관측이다 -------------------------------
          `<details>` 를 쓴다. 열림/닫힘 상태를 리액트가 들 이유가 없고,
          키보드·스크린리더 동작이 공짜로 따라온다. `open` 이라 처음엔 펼쳐져
          있다 — 접힌 채로 두면 이 프로젝트의 차별점을 아무도 안 열어본다. */}
      <details className="pipeline" open>
        <summary>
          파이프라인
          <span className="muted">
            {" "}
            — 데모용 관측 패널입니다. 실제 서비스에서는 노출하지 않습니다.
          </span>
        </summary>

        <div className="row" style={{ marginTop: 10 }}>
          <span className="badge">
            의도 <strong>{data.intent}</strong>
          </span>
          <span className="badge">
            판정 <strong>{data.routing.decided_by}</strong>
            {data.routing.confidence != null && ` · ${data.routing.confidence}`}
          </span>
          {escalated && (
            <span className="badge warn">
              에스컬레이션{" "}
              <strong>
                {data.routing.initial_intent} → {data.intent}
              </strong>
            </span>
          )}
          <span className={`badge ${data.cache.hit ? "hit" : ""}`}>
            캐시{" "}
            <strong>
              {data.cache.hit ? `적중 (${data.cache.match_type})` : "미적중"}
            </strong>
          </span>
          {/* 적중인데 호출이 0이 아니면 회귀다 — 그 판정을 색으로 낸다. */}
          <span
            className={`badge ${data.cache.hit && data.llm_calls > 0 ? "fail" : ""}`}
          >
            LLM 호출 <strong>{data.llm_calls}회</strong>
          </span>
          {/* 0건은 LLM을 안 거친 확정 응답이다. 설명 생성 호출이 빠져서 2회가
              아니라 1회여야 한다 — 2회면 회귀다. */}
          {data.no_results && (
            <span className={`badge ${data.llm_calls > 1 ? "fail" : "hit"}`}>
              결과 없음 <strong>LLM 설명 생략</strong>
            </span>
          )}
          {/* **거절도 파이프라인의 동작이다** (ADR-0039). 이게 없으면 화면에는
              안내 문장 하나만 남아서, 게이트가 판단한 것인지 그냥 검색이
              실패한 것인지 구분되지 않는다. `no_results` 와 나란히 두는 이유는
              둘이 서로 다른 판정이기 때문이다 — 동시에 뜰 일은 없다. */}
          {data.out_of_domain && (
            <span className={`badge ${data.llm_calls > 1 ? "fail" : "hit"}`}>
              도메인 밖 <strong>검색·설명 생략</strong>
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

        {data.tool_calls && data.tool_calls.length > 0 && (
          <>
            <div className="muted" style={{ margin: "10px 0 6px" }}>
              에이전트 도구 호출 — MCP 서버를 거칩니다
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
          </>
        )}
      </details>
    </div>
  );
}

/**
 * 아이템 카드 — 검색 결과와 `resolved_item` 이 **같은 것을 쓴다.**
 *
 * 따로 두면 한쪽만 고쳐지고 같은 아이템이 화면마다 다르게 보인다. 실제로
 * 그렇게 시작했다가 되돌렸다.
 */
function ItemCard({
  item,
  onOpen,
}: {
  item: SearchResultItem;
  onOpen: (itemId: number) => void;
}) {
  return (
    <div className="item-card" onClick={() => onOpen(item.item_id)}>
      <h4>{item.name}</h4>
      {item.price != null && <div className="price">{formatWon(item.price)}</div>}
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
  );
}
