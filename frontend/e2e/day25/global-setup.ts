/**
 * Runs once per `playwright test -c playwright.prod.config.ts` invocation —
 * not per worker — so the findings in `test-results/day25/` always come from
 * exactly one run. See `reports.ts`.
 */

import { clearReports } from "./reports";

export default function globalSetup(): void {
  const removed = clearReports();
  if (removed.length > 0) {
    process.stdout.write(`day25: cleared ${removed.length} report file(s) from a previous run\n`);
  }
}
