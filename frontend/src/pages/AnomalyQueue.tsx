import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { api, formatWon, type AnomalyAlert } from "../api";

/**
 * GM 이상거래 검토 큐.
 *
 * Phase 5-2에서 오토인코더를 Isolation Forest 대신 고른 이유가 설명가능성
 * 하나였는데, 그 결과가 화면에 드러나는 유일한 지점이다. 점수만 보여주면
 * 그 선택의 근거가 사라진다.
 */
export default function AnomalyQueue() {
  const queue = useQuery({
    queryKey: ["alerts"],
    queryFn: () => api.alerts(15),
  });

  if (queue.isPending) return <p className="muted">알림을 불러오는 중…</p>;
  if (queue.isError) return <p className="error">{(queue.error as Error).message}</p>;

  const data = queue.data;

  return (
    <div className="stack">
      <div className="row">
        <span className="badge">
          전체 거래 <strong>{data.total_trades.toLocaleString("ko-KR")}건</strong>
        </span>
        <span className="badge">
          알림 <strong>{data.total_alerts}건</strong>
        </span>
        <span className="badge">
          임계값 <strong>{data.threshold}</strong> (p{data.alert_percentile})
        </span>
      </div>

      <p className="muted" style={{ margin: 0 }}>
        임계값은 학습에 쓰지 않은 정상 데이터에서 산정했습니다. 점수 순 정렬은
        가격 이상치에 치우치는 알려진 한계가 있습니다(ADR-0009).
      </p>

      <p className="error" style={{ margin: 0, fontSize: 13 }}>
        아래 <strong>합성#</strong> 번호는 AI 데모 데이터의 거래·유저 id입니다.
        실제 거래 내역과 번호 범위가 겹치지만 <strong>서로 다른 대상</strong>이므로,
        이 번호를 거래 조회 등 다른 화면에 입력하지 마세요. 실거래 연동은 Phase 8
        과제입니다.
      </p>

      <div className="stack">
        {data.alerts.map((alert) => (
          <AlertCard key={alert.trade_id} alert={alert} />
        ))}
      </div>
    </div>
  );
}

function AlertCard({ alert }: { alert: AnomalyAlert }) {
  // 합성 id는 백엔드 거래·유저 id와 범위가 겹치면서도 다른 대상을 가리킨다.
  // 접두사를 붙여 사용자가 이 번호를 자기 거래 조회에 넣지 않게 한다.
  // (아이템 id는 시딩으로 양쪽을 맞춰뒀으므로 링크가 유효하다.)
  const synthetic = alert.id_space === "synthetic";
  const ref = (value: number) => (synthetic ? `합성#${value}` : `#${value}`);

  return (
    <div className="card stack" style={{ gap: 10 }}>
      <div className="row">
        <strong>거래 {ref(alert.trade_id)}</strong>
        <Link to={`/items/${alert.item_id}`} className="muted">
          아이템 #{alert.item_id} →
        </Link>
        <span className="badge fail">
          이상 점수 <strong>{alert.anomaly_score.toFixed(1)}</strong>
        </span>
        <span className="badge">
          시세 대비 <strong>{alert.price_ratio.toFixed(2)}배</strong>
        </span>
        {/* 더미 데이터라 정답 라벨을 알고 있다. 실서비스에는 없는 필드이고,
            판정이 맞았는지 눈으로 확인하라고 남긴다. */}
        {alert.injected_label && (
          <span className="badge warn">주입 유형 {alert.injected_label}</span>
        )}
      </div>

      <div className="muted">
        {formatWon(alert.price)} · 시세 중앙값 {formatWon(alert.market_median)} ·{" "}
        구매자 {ref(alert.buyer_id)} / 판매자 {ref(alert.seller_id)} ·{" "}
        {alert.traded_at.replace("T", " ").slice(0, 16)}
      </div>

      <div>
        <div className="muted" style={{ marginBottom: 6 }}>
          이상 판정 기여도
        </div>
        {alert.contributions.map((contribution) => (
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
      </div>
    </div>
  );
}
