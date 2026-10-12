/**
 * Where the Day 25 specs write their findings, and the one function that
 * empties it before a run (Day 25 verification).
 *
 * The axe spec *appends* JSON lines as it goes (Playwright restarts the worker
 * after a failing test, so no module can hold a whole run), and the keyboard
 * and zoom specs write one file per project or mode. Playwright only cleans
 * its own `outputDir` (`test-results/day25/artifacts`), so without this a
 * second local run added its scans to the first run's, and a stale
 * `keyboard-*.json` from an older run could sit next to fresh ones — a
 * verification report quoting a mixture of runs. `global-setup.ts` calls
 * {@link clearReports} once per `playwright test` invocation.
 *
 * No Playwright imports here: the unit test (`src/lib/day25Reports.test.ts`)
 * runs this module in Vitest.
 */

import { mkdirSync, readdirSync, rmSync } from "node:fs";
import { join } from "node:path";

/** Relative to the frontend directory, where Playwright runs. */
export const REPORT_DIR = "test-results/day25";

/** The files the specs write: `axe-<project>.jsonl`, `keyboard-<project>.json`, `zoom-<mode>.json`. */
export const REPORT_FILE = /^(?:axe-.+\.jsonl?|keyboard-.+\.json|zoom-.+\.json)$/;

/**
 * Delete the previous run's findings; leave everything else (Playwright's
 * `results.json`, the artifacts folder) alone. Returns what was removed.
 */
export function clearReports(dir: string = REPORT_DIR): string[] {
  mkdirSync(dir, { recursive: true });
  const removed = readdirSync(dir)
    .filter((name) => REPORT_FILE.test(name))
    .sort();
  for (const name of removed) {
    rmSync(join(dir, name), { force: true });
  }
  return removed;
}
