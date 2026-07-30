import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { formatWon, type Forecast } from "../api";

/**
 * 시세 실적 + 예측 라인 차트.
 *
 * 두 계열 모두 "원" 단위라 **축은 하나**다(이중 축 금지). 실적은 실선,
 * 예측은 파선으로 구분해서 색 말고도 구분이 서게 했다.
 *
 * 색은 dataviz 스킬의 검증된 카테고리 슬롯 1·2이고, 라이트/다크 두 모드에서
 * validate_palette.js 전 항목을 통과했다(인접 CVD ΔE 24.7 / 26.8, 기준 8).
 * CSS 변수로 참조하므로 다크 모드 전환이 styles.css 한 곳에서 끝난다.
 */
export default function PriceChart({ forecast }: { forecast: Forecast }) {
  // 두 계열을 한 x축에 올린다. 실적의 마지막 점을 예측에도 넣어야 선이
  // 끊기지 않고 이어진다.
  const lastHistory = forecast.history.at(-1);
  const rows = [
    ...forecast.history.map((point) => ({
      date: point.date.slice(5),
      actual: point.price,
      predicted: null as number | null,
    })),
    ...forecast.forecast.map((point, index) => ({
      date: point.date.slice(5),
      actual: null as number | null,
      predicted: point.price,
      ...(index === 0 && lastHistory ? {} : {}),
    })),
  ];
  if (lastHistory && rows.length > forecast.history.length) {
    rows[forecast.history.length - 1].predicted = lastHistory.price;
  }

  return (
    <div style={{ width: "100%", height: 260 }}>
      <ResponsiveContainer>
        <LineChart data={rows} margin={{ top: 8, right: 12, bottom: 0, left: 8 }}>
          <CartesianGrid stroke="var(--grid)" vertical={false} />
          <XAxis
            dataKey="date"
            stroke="var(--axis)"
            tick={{ fill: "var(--text-muted)", fontSize: 11 }}
            minTickGap={24}
          />
          <YAxis
            stroke="var(--axis)"
            tick={{ fill: "var(--text-muted)", fontSize: 11 }}
            width={64}
            tickFormatter={(value: number) =>
              `${Math.round(value / 1000).toLocaleString("ko-KR")}k`
            }
            domain={["auto", "auto"]}
          />
          <Tooltip
            cursor={{ stroke: "var(--axis)", strokeWidth: 1 }}
            content={({ active, payload, label }) => {
              if (!active || !payload?.length) return null;
              return (
                <div className="tooltip">
                  <div className="muted">{label}</div>
                  {payload.map((entry) => (
                    <div key={entry.name}>
                      {entry.name}: <strong>{formatWon(Number(entry.value))}</strong>
                    </div>
                  ))}
                </div>
              );
            }}
          />
          <Legend
            wrapperStyle={{ fontSize: 12, color: "var(--text-secondary)" }}
          />
          <Line
            name="실적"
            type="monotone"
            dataKey="actual"
            stroke="var(--series-1)"
            strokeWidth={2}
            dot={false}
            activeDot={{ r: 4 }}
            connectNulls={false}
          />
          <Line
            name="예측"
            type="monotone"
            dataKey="predicted"
            stroke="var(--series-2)"
            strokeWidth={2}
            strokeDasharray="5 4"
            dot={false}
            activeDot={{ r: 4 }}
            connectNulls={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
