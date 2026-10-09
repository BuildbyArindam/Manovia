import type { ReactElement } from "react";

import { PageHeader } from "@/components/PageHeader";

/** Mood — placeholder for Day 5. */
export function MoodPage(): ReactElement {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Mood"
        lede="A thirty-second check-in: how you feel, and what might be behind it."
      />
      <section className="rounded-2xl border border-border bg-surface p-6 shadow-soft">
        <h2 className="text-lg">Coming next</h2>
        <ul className="mt-3 list-disc space-y-2 pl-6 text-ink-muted">
          <li>A valence and energy scale, with the words you would actually use.</li>
          <li>Optional factors — sleep, work, people, weather, nothing at all.</li>
          <li>A private note that is encrypted before it leaves your device.</li>
        </ul>
      </section>
    </div>
  );
}
