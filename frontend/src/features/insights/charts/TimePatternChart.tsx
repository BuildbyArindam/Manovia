/**
 * Time pattern chart (hourly or weekday) with text summary and data table alternative.
 */

import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import { useId, useMemo } from "react";

import type { TimePattern } from "../types";
import { CHART_PALETTE } from "./ChartPalette";
import type { ChartTooltipProps } from "./tooltip";

/** One row of the chart's datum, also the shape the tooltip receives. */
interface TimePatternDatum {
  bucket: string;
  valence: number | null;
  energy: number | null;
  count: number;
  note: string;
  hasData: boolean;
}

interface TimePatternChartProps {
  data: TimePattern[];
  title: string;
  subheading: string;
  xAxisLabel: string;
  ariaLabel?: string;
}

export function TimePatternChart({
  data,
  title,
  subheading,
  xAxisLabel,
  ariaLabel,
}: TimePatternChartProps) {
  const headingId = useId();
  const chartData = useMemo(
    () =>
      data.map((d) => ({
        bucket: d.bucket,
        valence: d.valence_avg,
        energy: d.energy_avg,
        count: d.count,
        note: d.note,
        hasData: d.valence_avg !== null || d.energy_avg !== null,
      })),
    [data],
  );

  const hasData = chartData.some((d) => d.hasData);

  // Text summary
  const textSummary = useMemo(() => {
    if (!hasData) {
      return `No time patterns to show yet. Need at least 5 check-ins per time bucket.`;
    }
    const withData = chartData.filter((d) => d.hasData);
    let bestValence: { bucket: string; valence: number } | null = null;
    let worstValence: { bucket: string; valence: number } | null = null;
    for (const d of withData) {
      if (d.valence !== null) {
        if (bestValence === null || d.valence > bestValence.valence) {
          bestValence = { bucket: d.bucket, valence: d.valence };
        }
        if (worstValence === null || d.valence < worstValence.valence) {
          worstValence = { bucket: d.bucket, valence: d.valence };
        }
      }
    }
    return (
      `${withData.length} of ${data.length} time buckets have enough data. ` +
      (bestValence
        ? `Highest average mood at ${bestValence.bucket} (${bestValence.valence.toFixed(1)}). `
        : "") +
      (worstValence
        ? `Lowest at ${worstValence.bucket} (${worstValence.valence.toFixed(1)}). `
        : "") +
      "These are descriptive patterns, not predictions."
    );
  }, [chartData, hasData, data.length]);

  const CustomTooltip = ({ active, payload }: ChartTooltipProps<TimePatternDatum>) => {
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
        <p style={{ margin: 0, fontWeight: 600 }}>{point.bucket}</p>
        {point.valence !== null && (
          <p style={{ margin: "4px 0 0", color: CHART_PALETTE.valence }}>
            Valence: {point.valence.toFixed(1)}
          </p>
        )}
        {point.energy !== null && (
          <p style={{ margin: "4px 0 0", color: CHART_PALETTE.energy }}>
            Energy: {point.energy.toFixed(1)}
          </p>
        )}
        <p style={{ margin: "4px 0 0", fontSize: "0.8rem", color: "var(--manovia-text-muted)" }}>
          {point.count} check-in(s)
        </p>
      </div>
    );
  };

  return (
    <section aria-labelledby={headingId} style={{ marginBottom: "24px" }}>
      <h3 id={headingId} style={{ marginBottom: "4px" }}>
        {title}
      </h3>
      <p style={{ marginBottom: "12px", color: "var(--manovia-text-muted)", fontSize: "0.9rem" }}>
        {subheading}
      </p>

      {/* Text summary — uniquely named, because every chart on this page has one. */}
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
        aria-label={ariaLabel ?? `${title} chart showing mood by ${xAxisLabel.toLowerCase()}`}
        style={{ height: 300 }}
      >
        {hasData ? (
          <ResponsiveContainer width="100%" height="100%">
            <BarChart
              data={chartData}
              margin={{ top: 10, right: 10, left: 0, bottom: 0 }}
            >
              <CartesianGrid
                strokeDasharray="3 3"
                stroke="var(--manovia-border)"
                vertical={false}
              />
              <XAxis
                dataKey="bucket"
                tick={{ fill: "var(--manovia-text-muted)", fontSize: 11 }}
                axisLine={{ stroke: "var(--manovia-border)" }}
                tickLine={{ stroke: "var(--manovia-border)" }}
              />
              <YAxis
                domain={["auto", "dataMax + 0.5"]}
                tick={{ fill: "var(--manovia-text-muted)", fontSize: 11 }}
                axisLine={{ stroke: "var(--manovia-border)" }}
                tickLine={{ stroke: "var(--manovia-border)" }}
                label={{ value: "Average (1–5)", position: "left", offset: -10 }}
              />
              <Tooltip content={<CustomTooltip />} />
              <Bar
                dataKey="valence"
                name="Valence"
                fill={CHART_PALETTE.valence}
                radius={[4, 4, 0, 0]}
                maxBarSize={30}
              />
              <Bar
                dataKey="energy"
                name="Energy"
                fill={CHART_PALETTE.energy}
                radius={[4, 4, 0, 0]}
                maxBarSize={30}
              />
            </BarChart>
          </ResponsiveContainer>
        ) : (
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              height: "100%",
              color: "var(--manovia-text-muted)",
              textAlign: "center",
              padding: "24px",
            }}
          >
            {textSummary}
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
                <th style={{ textAlign: "left", padding: "8px" }}>{xAxisLabel}</th>
                <th style={{ textAlign: "right", padding: "8px" }}>Valence</th>
                <th style={{ textAlign: "right", padding: "8px" }}>Energy</th>
                <th style={{ textAlign: "right", padding: "8px" }}>Check-ins</th>
                <th style={{ textAlign: "left", padding: "8px" }}>Note</th>
              </tr>
            </thead>
            <tbody>
              {data.map((d) => (
                <tr
                  key={d.bucket}
                  style={{ borderBottom: "1px solid var(--manovia-border)" }}
                >
                  <td style={{ padding: "8px", fontWeight: 500 }}>{d.bucket}</td>
                  <td style={{ padding: "8px", textAlign: "right" }}>
                    {d.valence_avg !== null ? d.valence_avg.toFixed(1) : "—"}
                  </td>
                  <td style={{ padding: "8px", textAlign: "right" }}>
                    {d.energy_avg !== null ? d.energy_avg.toFixed(1) : "—"}
                  </td>
                  <td style={{ padding: "8px", textAlign: "right" }}>{d.count}</td>
                  <td style={{ padding: "8px", fontSize: "0.8rem", color: "var(--manovia-text-muted)" }}>
                    {d.note}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </section>
  );
}