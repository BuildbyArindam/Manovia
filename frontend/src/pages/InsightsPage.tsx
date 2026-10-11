import type { ReactElement } from "react";

import { InsightsView } from "@/features/insights/InsightsView";

/** Insights — patterns in your own check-ins. The feature lives in `features/insights`. */
export function InsightsPage(): ReactElement {
  return <InsightsView />;
}
