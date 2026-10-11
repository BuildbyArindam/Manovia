/**
 * Export insights as CSV.
 */

import { useState } from "react";

import type { ApiClient } from "@/lib/api";

import { exportInsights } from "./endpoints";
import { INSIGHTS_COPY } from "./copy";

export interface ExportButtonProps {
  client?: ApiClient;
}

/**
 * Download the person's own data as CSV.
 *
 * Three files, one per section, because they have different shapes and a single
 * merged sheet would be worse than three readable ones. The button states are
 * announced through `aria-live` rather than colour alone: this is the one action
 * on the page that leaves the browser, so silence after a click would be
 * indistinguishable from a failure.
 */
export function ExportButton({ client }: ExportButtonProps = {}) {
  const [state, setState] = useState<"idle" | "loading" | "success" | "error">("idle");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const handleExport = async () => {
    setState("loading");
    setErrorMessage(null);
    try {
      const bundle = await exportInsights(client);

      // Create and download three CSV files
      const files = [
        { name: "mood-entries.csv", content: bundle.entries_csv },
        { name: "factor-associations.csv", content: bundle.factor_associations_csv },
        { name: "time-patterns.csv", content: bundle.time_patterns_csv },
      ];

      for (const file of files) {
        const blob = new Blob([file.content], { type: "text/csv;charset=utf-8;" });
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = file.name;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        URL.revokeObjectURL(url);
      }

      setState("success");
    } catch (err) {
      setState("error");
      setErrorMessage(err instanceof Error ? err.message : "Export failed");
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
      <button
        type="button"
        onClick={handleExport}
        disabled={state === "loading"}
        style={{
          minHeight: "48px",
          padding: "10px 16px",
          background: "var(--manovia-accent)",
          color: "var(--manovia-surface)",
          border: "none",
          borderRadius: "8px",
          fontWeight: 600,
          cursor: state === "loading" ? "wait" : "pointer",
          opacity: state === "loading" ? 0.7 : 1,
        }}
      >
        {state === "loading" ? "Preparing…" : INSIGHTS_COPY.exportButton}
      </button>
      {/* One polite region for both outcomes: success and failure are announced
          the same way, because a silent button is the failure mode that hurts. */}
      <p
        role="status"
        aria-live="polite"
        style={{
          margin: 0,
          fontSize: "0.85rem",
          color: state === "error" ? "var(--manovia-error)" : "var(--manovia-text-muted)",
          minHeight: "1.2em",
        }}
      >
        {state === "success" && INSIGHTS_COPY.exportSuccess}
        {state === "error" &&
          `${INSIGHTS_COPY.exportError}${errorMessage === null ? "" : `: ${errorMessage}`}`}
      </p>
    </div>
  );
}
