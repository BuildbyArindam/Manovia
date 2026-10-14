/**
 * Print every failing Day-25 spec as a GitHub Actions annotation.
 *
 * Why this exists: the a11y job's Playwright step fails intermittently, and the
 * only place the failing spec's name appears is the job log — which GitHub
 * serves from a blob store that a restricted network cannot reach, and which
 * `gh run view --log` does not return either. The step's annotations say only
 * "Process completed with exit code 1", which is true and useless.
 *
 * Playwright already writes `test-results/day25/results.json` on failure (the
 * json reporter runs whether the run passes or not), so the information is one
 * file away. This reads it and emits `::error` workflow commands, which GitHub
 * turns into annotations that `gh api .../check-runs/<id>/annotations` returns.
 *
 * Usage: node scripts/playwright-failures.mjs test-results/day25/results.json
 *
 * Exits 0 always: this is a diagnostic, and a diagnostic must never be the
 * reason a job fails.
 */

import { existsSync, readFileSync } from "node:fs";

const MAX_LINES = 8;

/**
 * Collect every spec in a Playwright JSON report, depth-first.
 *
 * @param {unknown} suite
 * @returns {Array<Record<string, any>>}
 */
function collectSpecs(suite) {
  if (suite === null || typeof suite !== "object") {
    return [];
  }
  /** @type {Record<string, any>} */
  const node = /** @type {Record<string, any>} */ (suite);
  const own = Array.isArray(node.specs) ? node.specs : [];
  const nested = Array.isArray(node.suites) ? node.suites.flatMap(collectSpecs) : [];
  return [...own, ...nested];
}

/**
 * The result that made a spec fail, or `null` when it did not fail.
 *
 * A spec fails when any of its tests (one per project) has a result whose status
 * is neither `passed` nor `skipped`. `timedOut` and `failed` are the two that
 * matter, and both carry an `error` we want to show.
 *
 * @param {Record<string, any>} spec
 * @returns {{ status: string, message: string } | null}
 */
function failureOf(spec) {
  const tests = Array.isArray(spec.tests) ? spec.tests : [];
  for (const test of tests) {
    const results = Array.isArray(test.results) ? test.results : [];
    for (const result of results) {
      const status = String(result?.status ?? "");
      if (status !== "passed" && status !== "skipped") {
        return { status, message: String(result?.error?.message ?? "(no message)") };
      }
    }
  }
  return null;
}

/**
 * Collapse a Playwright error message to the lines that name the cause.
 *
 * Playwright appends the call log and a diff of the received/expected values,
 * which is the part a reader actually needs — but it also prepends a blank line
 * and pads, so trim and cap.
 *
 * @param {string} message
 * @returns {string}
 */
function summarise(message) {
  const lines = message
    .split("\n")
    .map((line) => line.trimEnd())
    .filter((line) => line.length > 0)
    .slice(0, MAX_LINES);
  return lines.join("\n");
}

const path = process.argv[2] ?? "test-results/day25/results.json";

if (!existsSync(path)) {
  console.log(`playwright-failures: ${path} not found; nothing to report`);
  process.exit(0);
}

/** @type {Record<string, any>} */
let report;
try {
  report = JSON.parse(readFileSync(path, "utf8"));
} catch (error) {
  console.log(`playwright-failures: ${path} is not valid JSON (${String(error)})`);
  process.exit(0);
}

const specs = collectSpecs(report);
const failed = specs
  .map((spec) => ({ spec, failure: failureOf(spec) }))
  .filter((entry) => entry.failure !== null);

if (failed.length === 0) {
  console.log(`playwright-failures: ${specs.length} spec(s), none failed`);
  process.exit(0);
}

for (const { spec, failure } of failed) {
  const title = String(spec.title ?? "(untitled)");
  const file = String(spec.file ?? "(unknown file)");
  const projects = (Array.isArray(spec.tests) ? spec.tests : [])
    .map((test) => String(test.projectName ?? ""))
    .filter((name) => name.length > 0)
    .join(", ");
  console.log(`::error file=${file}::${title} [${projects || "no project"}] ${failure.status}`);
  console.log(`  ${file} > ${title}`);
  console.log(`  ${summarise(failure.message)}`);
  console.log("");
}

console.log(`playwright-failures: ${failed.length} of ${specs.length} spec(s) failed`);
