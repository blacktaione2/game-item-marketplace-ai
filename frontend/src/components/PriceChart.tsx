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
/**
 * y축 눈금 라벨.
 *
 * **`Math.round(v / 1000) + "k"` 하나로 쓰다가 축이 정보를 잃었다.** 콜드스타트
 * 아이템은 변동폭이 좁게 나오는데(실측: 22,000~22,800원), 그 범위에서는 모든
 * 눈금이 같은 값으로 반올림된다 — 배포 화면의 y축이 위에서부터
 * `23k · 22k · 22k · 22k · 22k` 였다. **라벨이 전부 같은 축은 없는 축보다 나쁘다**
 * (있으니까 읽으려 하고, 읽어도 아무것도 안 나온다).
 *
 * 그래서 **범위를 보고 표기를 고른다.** 넓으면 `k` 로 줄이고(백만 단위 아이템이
 * 축을 밀어내지 않게), 좁으면 원 단위를 그대로 쓴다. 소수 자릿수를 늘리는
 * 안(`22.4k`)도 있었지만, 범위가 더 좁아지면 같은 문제가 한 자리 뒤에서 재발한다 —
 * 자릿수를 늘리는 것은 **경계를 옮길 뿐 없애지 못한다.**
 *
 * 경계값 10,000원은 이 축의 눈금이 보통 5개라는 데서 온다: 범위가 그보다 넓으면
 * 눈금 간격이 2,000원 이상이라 `k` 표기로도 반드시 갈린다.
 */
export function axisTick(span: number): (value: number) => string {
  return (value) =>
    span >= 10000
      ? `${Math.round(value / 1000).toLocaleString("ko-KR")}k`
      : value.toLocaleString("ko-KR");
}

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

  // 실제로 그려지는 값들의 범위. 눈금 표기를 여기서 고른다(`axisTick` 주석 참고).
  const values = rows.flatMap((row) =>
    [row.actual, row.predicted].filter((v): v is number => v !== null),
  );
  const span = values.length ? Math.max(...values) - Math.min(...values) : 0;

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
            // 원 단위 전체 표기(`1,200,000`)까지 들어갈 수 있어야 잘리지 않는다.
            width={72}
            tickFormatter={axisTick(span)}
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
