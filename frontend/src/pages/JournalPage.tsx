import type { ReactElement } from "react";

import { PageHeader } from "@/components/PageHeader";

/** Journal — placeholder for Day 5. */
export function JournalPage(): ReactElement {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Journal"
        lede="Write as much or as little as you want. No prompts you have to answer."
      />
      <section className="rounded-2xl border border-border bg-surface p-6 shadow-soft">
        <h2 className="text-lg">Coming next</h2>
        <ul className="mt-3 list-disc space-y-2 pl-6 text-ink-muted">
          <li>Free-form entries with an optional title.</li>
          <li>Encrypted at rest, in a column that has no plaintext sibling.</li>
          <li>A gentle summary of themes over time, never a diagnosis.</li>
        </ul>
      </section>
    </div>
  );
}
