/**
 * Factor association chart with text summary and data table alternative.
 */

import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
  ReferenceLine,
} from "recharts";
import { useId, useMemo } from "react";

import type { FactorAssociation } from "../types";
import { getCategoricalColour } from "./ChartPalette";
import type { ChartTooltipProps } from "./tooltip";

/** One row of the chart's datum, also the shape the tooltip receives. */
interface FactorDatum {
  factor: string;
  withFactor: number | null;
  withoutFactor: number | null;
  withCount: number;
  withoutCount: number;
  effectSize: number | null;
  pValue: number | null;
  color: string;
  hasEnoughData: boolean;
}

interface FactorAssociationChartProps {
  data: FactorAssociation[];
  minPerGroup: number;
  title?: string;
  ariaLabel?: string;
}

export function FactorAssociationChart({
  data,
  minPerGroup,
  title = "Factor associations",
  ariaLabel,
}: FactorAssociationChartProps) {
  const headingId = useId();
  const chartData = useMemo(
    () =>
      data.map((a, i) => ({
        factor: a.factor,
        withFactor: a.with_factor_avg,
        withoutFactor: a.without_factor_avg,
        withCount: a.with_factor_count,
        withoutCount: a.without_factor_count,
        effectSize: a.effect_size,
        ciLower: a.ci_lower,
        ciUpper: a.ci_upper,
        pValue: a.p_value,
        note: a.note,
        color: getCategoricalColour(i),
        hasEnoughData:
          a.with_factor_count >= minPerGroup && a.without_factor_count >= minPerGroup,
      })),
    [data, minPerGroup],
  );

  const hasEnoughData = chartData.some((d) => d.hasEnoughData);

  // Text summary
  const textSummary = useMemo(() => {
    if (!hasEnoughData) {
      return `Not enough data for any factor comparison. Each factor needs at least ${minPerGroup} days with and without the tag.`;
    }
    const withData = chartData.filter((d) => d.hasEnoughData);
    const parts = withData.map((d) => {
      const diff =
        d.withFactor !== null && d.withoutFactor !== null
          ? d.withFactor - d.withoutFactor
          : 0;
      const direction = diff > 0 ? "higher" : diff < 0 ? "lower" : "similar";
      return `${d.factor}: ${Math.abs(diff).toFixed(1)} points ${direction} on days with the tag (n=${d.withCount} vs ${d.withoutCount})`;
    });
    return (
      `${withData.length} factor(s) have enough data for comparison. ` +
      parts.join("; ") +
      ". These are associations, not causal links."
    );
  }, [chartData, hasEnoughData, minPerGroup]);

  const CustomTooltip = ({ active, payload }: ChartTooltipProps<FactorDatum>) => {
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
        <p style={{ margin: 0, fontWeight: 600 }}>{point.factor}</p>
        {point.withFactor !== null && (
          <p style={{ margin: "4px 0 0" }}>
            With tag: {point.withFactor.toFixed(1)} (n={point.withCount})
          </p>
        )}
        {point.withoutFactor !== null && (
          <p style={{ margin: "4px 0 0" }}>
            Without tag: {point.withoutFactor.toFixed(1)} (n={point.withoutCount})
          </p>
        )}
        {point.effectSize !== null && (
          <p style={{ margin: "4px 0 0" }}>
            Effect size (Cohen's d): {point.effectSize.toFixed(2)}
          </p>
        )}
        {point.pValue !== null && (
          <p style={{ margin: "4px 0 0" }}>
            p = {point.pValue.toFixed(3)}
          </p>
        )}
      </div>
    );
  };

  return (
    <section aria-labelledby={headingId} style={{ marginBottom: "24px" }}>
      <h3 id={headingId} style={{ marginBottom: "8px" }}>
        {title}
      </h3>

      <p style={{ marginBottom: "12px", color: "var(--manovia-text-muted)", fontSize: "0.9rem" }}>
        Average mood on days with vs without each context tag. This is an association, not causation.
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
        aria-label={ariaLabel ?? "Bar chart comparing mood with and without each factor tag"}
        style={{ height: 300 }}
      >
        {hasEnoughData ? (
          <ResponsiveContainer width="100%" height="100%">
            <BarChart
              data={chartData.filter((d) => d.hasEnoughData)}
              margin={{ top: 10, right: 10, left: 0, bottom: 0 }}
              layout="vertical"
            >
              <CartesianGrid
                strokeDasharray="3 3"
                stroke="var(--manovia-border)"
                horizontal={false}
              />
              <XAxis
                type="number"
                domain={["dataMin - 0.5", "dataMax + 0.5"]}
                tick={{ fill: "var(--manovia-text-muted)", fontSize: 11 }}
                axisLine={{ stroke: "var(--manovia-border)" }}
                tickLine={{ stroke: "var(--manovia-border)" }}
                label={{ value: "Average valence (1–5)", position: "bottom", offset: 30 }}
              />
              <YAxis
                type="category"
                dataKey="factor"
                width={100}
                tick={{ fill: "var(--manovia-text)", fontSize: 12 }}
                axisLine={false}
                tickLine={false}
              />
              <Tooltip content={<CustomTooltip />} />
              <Legend wrapperStyle={{ paddingTop: "20px" }} />
              {chartData.filter((d) => d.hasEnoughData).map((d) => (
                <Bar
                  key={d.factor}
                  dataKey="withFactor"
                  name="With tag"
                  fill={d.color}
                  radius={[0, 4, 4, 0]}
                  maxBarSize={24}
                />
              ))}
              {chartData.filter((d) => d.hasEnoughData).map((d) => (
                <Bar
                  key={`${d.factor}-without`}
                  dataKey="withoutFactor"
                  name="Without tag"
                  fill={d.color}
                  fillOpacity={0.4}
                  radius={[0, 4, 4, 0]}
                  maxBarSize={24}
                />
              ))}
              <ReferenceLine x={3} stroke="var(--manovia-text-muted)" strokeDasharray="2 2" />
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
                <th style={{ textAlign: "left", padding: "8px" }}>Factor</th>
                <th style={{ textAlign: "right", padding: "8px" }}>With tag avg</th>
                <th style={{ textAlign: "right", padding: "8px" }}>Without tag avg</th>
                <th style={{ textAlign: "right", padding: "8px" }}>With count</th>
                <th style={{ textAlign: "right", padding: "8px" }}>Without count</th>
                <th style={{ textAlign: "right", padding: "8px" }}>Effect size (d)</th>
                <th style={{ textAlign: "right", padding: "8px" }}>95% CI</th>
                <th style={{ textAlign: "right", padding: "8px" }}>p-value</th>
              </tr>
            </thead>
            <tbody>
              {data.map((a) => (
                <tr
                  key={a.factor}
                  style={{ borderBottom: "1px solid var(--manovia-border)" }}
                >
                  <td style={{ padding: "8px", fontWeight: 500 }}>{a.factor}</td>
                  <td style={{ padding: "8px", textAlign: "right" }}>
                    {a.with_factor_avg !== null ? a.with_factor_avg.toFixed(1) : "—"}
                  </td>
                  <td style={{ padding: "8px", textAlign: "right" }}>
                    {a.without_factor_avg !== null ? a.without_factor_avg.toFixed(1) : "—"}
                  </td>
                  <td style={{ padding: "8px", textAlign: "right" }}>{a.with_factor_count}</td>
                  <td style={{ padding: "8px", textAlign: "right" }}>{a.without_factor_count}</td>
                  <td style={{ padding: "8px", textAlign: "right" }}>
                    {a.effect_size !== null ? a.effect_size.toFixed(2) : "—"}
                  </td>
                  <td style={{ padding: "8px", textAlign: "right" }}>
                    {a.ci_lower !== null && a.ci_upper !== null
                      ? `[${a.ci_lower.toFixed(2)}, ${a.ci_upper.toFixed(2)}]`
                      : "—"}
                  </td>
                  <td style={{ padding: "8px", textAlign: "right" }}>
                    {a.p_value !== null ? a.p_value.toFixed(3) : "—"}
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