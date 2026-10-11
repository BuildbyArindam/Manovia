import type { ReactElement } from "react";

import { PageHeader } from "@/components/PageHeader";
import { InsightsView } from "@/features/insights";

/**
 * Insights — mood trends, factor associations, and time patterns.
 *
 * Every chart has a text summary and a data table alternative. All calculations
 * are deterministic and run on the server; no LLM is involved.
 */
export function InsightsPage(): ReactElement {
  return (
    <div className="space-y-6">
      <PageHeader title="Insights" lede="Patterns in your check-ins, shown honestly." />
      <InsightsView />
    </div>
  );
}