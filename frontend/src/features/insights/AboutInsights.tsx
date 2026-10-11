/**
 * "About these insights" explainer with limitations.
 */

import { INSIGHTS_COPY } from "./copy";

export function AboutInsights() {
  return (
    <details
      style={{
        marginTop: "32px",
        padding: "20px",
        background: "var(--manovia-surface-elevated)",
        borderRadius: "12px",
        border: "1px solid var(--manovia-border)",
      }}
    >
      <summary
        style={{
          cursor: "pointer",
          fontWeight: 600,
          fontSize: "1.1rem",
          display: "flex",
          alignItems: "center",
          gap: "8px",
        }}
      >
        {INSIGHTS_COPY.aboutHeading}
      </summary>

      <div style={{ marginTop: "16px", lineHeight: 1.7 }}>
        <p style={{ margin: "0 0 12px" }}>
          <strong>{INSIGHTS_COPY.aboutDataSource}</strong>
        </p>

        <h4 style={{ margin: "16px 0 8px" }}>What these insights are</h4>
        <ul style={{ margin: 0, paddingLeft: "20px" }}>
          <li>Descriptive patterns in your own check-in data.</li>
          <li>Computed locally on the server — no external services, no AI models.</li>
          <li>Based only on your valence/energy scales and context tags (not your notes).</li>
        </ul>

        <h4 style={{ margin: "16px 0 8px" }}>What they are not</h4>
        <ul style={{ margin: "0 0 16px", paddingLeft: "20px" }}>
          <li>Not a diagnosis, risk score, or clinical assessment.</li>
          <li>Not a prediction of how you will feel.</li>
          <li>Not causal — a tag co-occurring with a mood level doesn't mean it caused it.</li>
        </ul>

        <h4 style={{ margin: "16px 0 8px" }}>Minimum-data rules</h4>
        <ul style={{ margin: 0, paddingLeft: "20px" }}>
          {INSIGHTS_COPY.aboutLimitations.map((limitation, i) => (
            <li key={i}>{limitation}</li>
          ))}
        </ul>

        <h4 style={{ margin: "16px 0 8px" }}>How the calculations work</h4>
        <ul style={{ margin: 0, paddingLeft: "20px" }}>
          <li>
            <strong>Rolling averages:</strong> Daily averages of valence and energy, with a
            linear trend fitted over 7 or 30 days. Trend direction only claimed with ≥ 7
            days of data.
          </li>
          <li>
            <strong>Factor associations:</strong> For each tag, compares average valence on
            days with vs without the tag. Reports Cohen's d (effect size), a bootstrap
            95% confidence interval, and a permutation test p-value. Requires ≥ 5 days
            per group.
          </li>
          <li>
            <strong>Time patterns:</strong> Average valence/energy by hour and weekday.
            Same minimum-data rule (≥ 5 check-ins per bucket).
          </li>
          <li>
            <strong>Weekly summary:</strong> Template-based text (English, Hindi, Bengali)
            summarising the last 7 days. No LLM involved.
          </li>
        </ul>

        <h4 style={{ margin: "16px 0 8px" }}>Privacy</h4>
        <ul style={{ margin: 0, paddingLeft: "20px" }}>
          <li>Your check-in notes are encrypted and never used for these calculations.</li>
          <li>All computation runs on the server; only aggregated results are returned.</li>
          <li>You can export all your data as CSV at any time.</li>
        </ul>

        <p style={{ margin: "16px 0 0", fontSize: "0.9rem", color: "var(--manovia-text-muted)" }}>
          If something looks wrong or you have questions, the data table alternative under
          each chart shows the exact numbers used.
        </p>
      </div>
    </details>
  );
}