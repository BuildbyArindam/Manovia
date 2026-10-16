import type { ReactElement } from "react";

import { BiofeedbackView } from "@/features/biofeedback/ui/BiofeedbackView";

/**
 * Biofeedback breathing with wearables (Day 42). The feature lives in
 * `features/biofeedback`; this page is the routed shell.
 */
export function BiofeedbackPage(): ReactElement {
  return <BiofeedbackView />;
}
