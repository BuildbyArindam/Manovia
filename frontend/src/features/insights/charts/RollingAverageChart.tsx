/**
 * Rolling average chart with text summary and data table alternative.
 */

import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
  ReferenceLine,
} from "recharts";
import { useId, useMemo } from "react";

import type { RollingWindow } from "../types";
import { CHART_PALETTE } from "./ChartPalette";
import type { ChartTooltipProps } from "./tooltip";

/** One row of the chart's datum, also the shape the tooltip receives. */
interface RollingDatum {
  day: string;
  valence: number | null;
  energy: number | null;
  count: number;
}

interface RollingAverageChartProps {
  data: RollingWindow;
  title?: string;
  ariaLabel?: string;
}

export function RollingAverageChart({
  data,
  title = "Rolling average",
  ariaLabel,
}: RollingAverageChartProps) {
  const { points, trend, trend_note, window_days } = data;
  // Two rolling charts render on the same page; a fixed heading id would be a
  // duplicate id and the section's name would resolve to the first one.
  const headingId = useId();

  const chartData = useMemo(
    () =>
      points.map((p) => ({
        day: p.day,
        valence: p.valence_avg,
        energy: p.energy_avg,
        count: p.count,
      })),
    [points],
  );

  const hasData = points.some((p) => p.valence_avg !== null || p.energy_avg !== null);

  // Text summary for screen readers and reduced-motion
  const textSummary = useMemo(() => {
    if (!hasData) {
      return `No check-ins in the last ${window_days} days.`;
    }
    const valencePoints = points.filter((p) => p.valence_avg !== null);
    const energyPoints = points.filter((p) => p.energy_avg !== null);
    const latestValence = valencePoints[valencePoints.length - 1]?.valence_avg;
    const latestEnergy = energyPoints[energyPoints.length - 1]?.energy_avg;
    const avgValence =
      valencePoints.reduce((s, p) => s + (p.valence_avg ?? 0), 0) /
      valencePoints.length;
    const avgEnergy =
      energyPoints.reduce((s, p) => s + (p.energy_avg ?? 0), 0) /
      energyPoints.length;

    let trendText = "";
    switch (trend) {
      case "up":
        trendText = "The trend is upward.";
        break;
      case "down":
        trendText = "The trend is downward.";
        break;
      case "flat":
        trendText = "The trend is stable.";
        break;
      default:
        trendText = trend_note;
    }

    return (
      `${window_days}-day rolling average over ${points.length} days with data. ` +
      `Average valence ${avgValence.toFixed(1)}, average energy ${avgEnergy.toFixed(1)}. ` +
      (latestValence != null
        ? `Most recent: valence ${latestValence.toFixed(1)}`
        : "") +
      (latestEnergy != null
        ? `, energy ${latestEnergy.toFixed(1)}`
        : "") +
      `. ${trendText}`
    );
  }, [points, trend, trend_note, window_days, hasData]);

  const CustomTooltip = ({ active, payload }: ChartTooltipProps<RollingDatum>) => {
    const first = payload?.[0];
    if (!active || first === undefined) return null;
    const point = first.payload;
    return (
      <div
        style={{
          background: "var(--manovia-surface)",
          border: "1px solid var(--manovia-border)",
          borderRadius: "8px",
          padding: "8px 12px",
          boxShadow: "0 4px 12px rgba(0,0,0,0.15)",
        }}
      >
        <p style={{ margin: 0, fontWeight: 600 }}>{point.day}</p>
        {payload?.map((entry, i) => (
          <p key={i} style={{ margin: "4px 0 0", color: entry.color }}>
            {entry.name}: {entry.value?.toFixed(1) ?? "—"}
          </p>
        ))}
      </div>
    );
  };

  return (
    <section aria-labelledby={headingId} style={{ marginBottom: "24px" }}>
      <h3 id={headingId} style={{ marginBottom: "8px" }}>
        {title} ({window_days}-day)
      </h3>

      {/* Text summary — always present, and named uniquely: several charts share
          this page, and two landmarks with the same name are indistinguishable. */}
      <div
        role="region"
        aria-label={`Text summary: ${title}`}
        style={{
          marginBottom: "12px",
          padding: "12px",
          background: "var(--manovia-surface-elevated)",
          borderRadius: "8px",
          border: "1px solid var(--manovia-border)",
          fontSize: "0.9rem",
          lineHeight: 1.5,
        }}
      >
        <strong>Summary: </strong>
        <span>{textSummary}</span>
      </div>

      {/* Chart */}
      <div
        role="img"
        aria-label={ariaLabel ?? `${title} chart showing valence and energy over ${window_days} days`}
        style={{ height: 300 }}
      >
        {hasData ? (
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={chartData} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
              <CartesianGrid
                strokeDasharray="3 3"
                stroke="var(--manovia-border)"
                vertical={false}
              />
              <XAxis
                dataKey="day"
                tick={{ fill: "var(--manovia-text-muted)", fontSize: 11 }}
                axisLine={{ stroke: "var(--manovia-border)" }}
                tickLine={{ stroke: "var(--manovia-border)" }}
              />
              <YAxis
                domain={[0.5, 5.5]}
                tick={{ fill: "var(--manovia-text-muted)", fontSize: 11 }}
                axisLine={{ stroke: "var(--manovia-border)" }}
                tickLine={{ stroke: "var(--manovia-border)" }}
                tickFormatter={(v) => (v === 1 ? "1" : v === 5 ? "5" : v.toString())}
              />
              <Tooltip content={<CustomTooltip />} />
              <Legend
                wrapperStyle={{ paddingTop: "20px" }}
                formatter={(value) => value}
              />
              <Line
                type="monotone"
                dataKey="valence"
                name="Valence"
                stroke={CHART_PALETTE.valence}
                strokeWidth={2}
                dot={{ r: 4, strokeWidth: 2 }}
                activeDot={{ r: 6 }}
                connectNulls={true}
              />
              <Line
                type="monotone"
                dataKey="energy"
                name="Energy"
                stroke={CHART_PALETTE.energy}
                strokeWidth={2}
                strokeDasharray="5 5"
                dot={{ r: 4, strokeWidth: 2 }}
                activeDot={{ r: 6 }}
                connectNulls={true}
              />
              {trend !== "not_enough_data" && trend !== "flat" && (
                <ReferenceLine
                  x={points.length - 1}
                  y={points[points.length - 1]?.valence_avg ?? 3}
                  stroke={trend === "up" ? CHART_PALETTE.trendUp : CHART_PALETTE.trendDown}
                  strokeWidth={1}
                  strokeDasharray="2 2"
                  label={{
                    value: trend === "up" ? "Trending up" : "Trending down",
                    position: "top",
                    fill: "var(--manovia-text-muted)",
                    fontSize: 10,
                  }}
                />
              )}
            </LineChart>
          </ResponsiveContainer>
        ) : (
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              height: "100%",
              color: "var(--manovia-text-muted)",
            }}
          >
            No data to display.
          </div>
        )}
      </div>

      {/* Data table alternative */}
      <details style={{ marginTop: "16px" }}>
        <summary style={{ cursor: "pointer", fontWeight: 600, marginBottom: "8px" }}>
          Data table alternative
        </summary>
        <div style={{ overflowX: "auto" }}>
          <table
            style={{
              width: "100%",
              borderCollapse: "collapse",
              fontSize: "0.85rem",
            }}
          >
            <thead>
              <tr style={{ borderBottom: "2px solid var(--manovia-border)" }}>
                <th style={{ textAlign: "left", padding: "8px" }}>Date</th>
                <th style={{ textAlign: "right", padding: "8px" }}>Valence</th>
                <th style={{ textAlign: "right", padding: "8px" }}>Energy</th>
                <th style={{ textAlign: "right", padding: "8px" }}>Check-ins</th>
              </tr>
            </thead>
            <tbody>
              {points.map((p) => (
                <tr
                  key={p.day}
                  style={{ borderBottom: "1px solid var(--manovia-border)" }}
                >
                  <td style={{ padding: "8px" }}>{p.day}</td>
                  <td style={{ padding: "8px", textAlign: "right" }}>
                    {p.valence_avg !== null ? p.valence_avg.toFixed(1) : "—"}
                  </td>
                  <td style={{ padding: "8px", textAlign: "right" }}>
                    {p.energy_avg !== null ? p.energy_avg.toFixed(1) : "—"}
                  </td>
                  <td style={{ padding: "8px", textAlign: "right" }}>{p.count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </section>
  );
}