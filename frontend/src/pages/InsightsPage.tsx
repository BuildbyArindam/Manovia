import type { ReactElement } from "react";

import { PageHeader } from "@/components/PageHeader";

/**
 * Insights — placeholder for Day 5.
 *
 * Worth stating plainly, even as a placeholder: this page shows patterns in what
 * you wrote, never an interpretation of who you are.
 */
export function InsightsPage(): ReactElement {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Insights"
        lede="Patterns in your own words, over weeks rather than moments."
      />
      <section className="rounded-2xl border border-border bg-surface p-6 shadow-soft">
        <h2 className="text-lg">Coming next</h2>
        <ul className="mt-3 list-disc space-y-2 pl-6 text-ink-muted">
          <li>Mood trends with the context you recorded alongside them.</li>
          <li>Which exercises you actually found useful.</li>
          <li>No scores, no labels, and nothing that could read as a diagnosis.</li>
        </ul>
      </section>
    </div>
  );
}
