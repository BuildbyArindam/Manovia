import type { ReactElement } from "react";

import { MoodView } from "@/features/mood/MoodView";

/** Mood — the check-in surface. The feature itself lives in `features/mood`. */
export function MoodPage(): ReactElement {
  return <MoodView />;
}
