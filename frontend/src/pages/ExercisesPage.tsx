import type { ReactElement } from "react";

import { PageHeader } from "@/components/PageHeader";

/** Exercises — placeholder for Day 5. */
export function ExercisesPage(): ReactElement {
  return (
    <div className="space-y-6">
      <PageHeader title="Exercises" lede="Short, practical things to try when your head is loud." />
      <section className="rounded-2xl border border-border bg-surface p-6 shadow-soft">
        <h2 className="text-lg">Coming next</h2>
        <ul className="mt-3 list-disc space-y-2 pl-6 text-ink-muted">
          <li>Breathing and grounding practices with a calm, unhurried pace.</li>
          <li>Written reflections adapted from established self-help methods.</li>
          <li>Everything respects your reduced-motion setting, including the timers.</li>
        </ul>
      </section>
    </div>
  );
}
