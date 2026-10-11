/**
 * Weekly summary card - template-based, no LLM.
 */

import type { WeeklySummary } from "./types";
import { INSIGHTS_COPY, trendLabel } from "./copy";

interface WeeklySummaryProps {
  data: WeeklySummary | null;
  loading?: boolean;
  error?: string | null;
}

export function WeeklySummaryCard({ data, loading, error }: WeeklySummaryProps) {
  if (loading) {
    return (
      <div
        style={{
          padding: "24px",
          background: "var(--manovia-surface-elevated)",
          borderRadius: "12px",
          border: "1px solid var(--manovia-border)",
          textAlign: "center",
          color: "var(--manovia-text-muted)",
        }}
      >
        {INSIGHTS_COPY.weeklyLoading}
      </div>
    );
  }

  if (error) {
    return (
      <div
        style={{
          padding: "24px",
          background: "var(--manovia-surface-elevated)",
          borderRadius: "12px",
          border: "1px solid var(--manovia-border)",
          color: "var(--manovia-error)",
        }}
      >
        <p style={{ margin: 0 }}>{INSIGHTS_COPY.weeklyError}</p>
        <p style={{ margin: "8px 0 0", fontSize: "0.9rem", color: "var(--manovia-text-muted)" }}>
          {error}
        </p>
      </div>
    );
  }

  if (!data || data.total_check_ins === 0) {
    return (
      <div
        style={{
          padding: "24px",
          background: "var(--manovia-surface-elevated)",
          borderRadius: "12px",
          border: "1px solid var(--manovia-border)",
          textAlign: "center",
        }}
      >
        <h3 style={{ margin: "0 0 8px" }}>{INSIGHTS_COPY.weeklyHeading}</h3>
        <p style={{ margin: 0, color: "var(--manovia-text-muted)" }}>
          {data?.text ?? INSIGHTS_COPY.emptyBody}
        </p>
      </div>
    );
  }

  const periodLabel = `${data.period_start} to ${data.period_end}`;

  return (
    <article
      style={{
        padding: "24px",
        background: "var(--manovia-surface-elevated)",
        borderRadius: "12px",
        border: "1px solid var(--manovia-border)",
      }}
    >
      <header style={{ marginBottom: "16px" }}>
        <h2 style={{ margin: "0 0 4px" }}>{INSIGHTS_COPY.weeklyHeading}</h2>
        <p style={{ margin: 0, color: "var(--manovia-text-muted)", fontSize: "0.9rem" }}>
          {periodLabel} • {data.total_check_ins} check-in(s)
        </p>
      </header>

      <div style={{ marginBottom: "16px", lineHeight: 1.6 }}>
        <p style={{ margin: 0 }}>{data.text}</p>
      </div>

      <dl
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))",
          gap: "12px",
          marginTop: "16px",
          paddingTop: "16px",
          borderTop: "1px solid var(--manovia-border)",
        }}
      >
        <div>
          <dt style={{ fontSize: "0.8rem", color: "var(--manovia-text-muted)", marginBottom: "4px" }}>
            Average valence
          </dt>
          <dd style={{ margin: 0, fontSize: "1.5rem", fontWeight: 600 }}>
            {data.valence_avg !== null ? data.valence_avg.toFixed(1) : "—"}
            <span style={{ fontSize: "0.9rem", fontWeight: 400, color: "var(--manovia-text-muted)" }}>
              /5
            </span>
          </dd>
        </div>
        <div>
          <dt style={{ fontSize: "0.8rem", color: "var(--manovia-text-muted)", marginBottom: "4px" }}>
            Average energy
          </dt>
          <dd style={{ margin: 0, fontSize: "1.5rem", fontWeight: 600 }}>
            {data.energy_avg !== null ? data.energy_avg.toFixed(1) : "—"}
            <span style={{ fontSize: "0.9rem", fontWeight: 400, color: "var(--manovia-text-muted)" }}>
              /5
            </span>
          </dd>
        </div>
        <div>
          <dt style={{ fontSize: "0.8rem", color: "var(--manovia-text-muted)", marginBottom: "4px" }}>
            Trend
          </dt>
          <dd style={{ margin: 0, fontWeight: 600 }}>{trendLabel(data.trend)}</dd>
        </div>
      </dl>

      {data.top_factors.length > 0 && (
        <div style={{ marginTop: "16px" }}>
          <p style={{ margin: "0 0 8px", fontSize: "0.9rem", color: "var(--manovia-text-muted)" }}>
            Most common tags:
          </p>
          <div style={{ display: "flex", flexWrap: "wrap", gap: "8px" }}>
            {data.top_factors.map(([factor, count]) => (
              <span
                key={factor}
                style={{
                  padding: "4px 10px",
                  background: "var(--manovia-surface)",
                  border: "1px solid var(--manovia-border)",
                  borderRadius: "999px",
                  fontSize: "0.85rem",
                }}
              >
                {factor} ({count})
              </span>
            ))}
          </div>
        </div>
      )}
    </article>
  );
}