/**
 * Tests for the a11y job's diagnostic step.
 *
 * `scripts/playwright-failures.mjs` exists because the failing spec's name is
 * otherwise only in a job log this project cannot always read, so these tests
 * spawn the real script as a child process — the contract that matters is what
 * it prints and what it exits with, not its internals.
 */

import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

const SCRIPT = join(process.cwd(), "scripts", "playwright-failures.mjs");

/** A minimal Playwright JSON report; `specs` may be nested via `suites`. */
function report(suites: unknown[]): string {
  return JSON.stringify({ config: { version: "1.0.0" }, suites });
}

function spec(
  title: string,
  file: string,
  statuses: Array<{ status: string; message?: string }>,
  projectName = "desktop-1280x800",
): unknown {
  return {
    title,
    file,
    tests: [
      {
        projectName,
        results: statuses.map((entry) => ({
          status: entry.status,
          ...(entry.message === undefined
            ? {}
            : { error: { message: entry.message } }),
        })),
      },
    ],
  };
}

let dir: string;

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "pw-failures-"));
});

afterEach(() => {
  rmSync(dir, { recursive: true, force: true });
});

/** Run the script against a fixture; never throws, because the script does not. */
function run(contents: string | null, name = "results.json"): string {
  const path = join(dir, name);
  if (contents !== null) {
    writeFileSync(path, contents);
  }
  return execFileSync("node", [SCRIPT, path], { encoding: "utf8" });
}

describe("playwright-failures", () => {
  it("reports a timed-out spec as an error annotation with its project", () => {
    const out = run(
      report([
        {
          title: "a11y.spec.ts",
          specs: [
            spec("every route, light/standard", "e2e/day25/a11y.spec.ts", [
              { status: "passed" },
            ]),
            spec("states: help dialog", "e2e/day25/a11y.spec.ts", [
              {
                status: "timedOut",
                message: "\n  Test timeout of 120000ms exceeded.\n  at a.ts:1:1\n",
              },
            ]),
          ],
        },
      ]),
    );

    expect(out).toContain(
      "::error file=e2e/day25/a11y.spec.ts::states: help dialog [desktop-1280x800] timedOut",
    );
    // The assertion text rides *inside* the annotation, because the annotation
    // is the part the API returns and a title alone says which spec, not why.
    expect(out).toContain("Test timeout of 120000ms exceeded.");
    expect(out).toContain("e2e/day25/a11y.spec.ts > states: help dialog");
    expect(out).toContain("playwright-failures: 1 of 2 spec(s) failed");
  });

  it("finds specs nested inside suites, and ignores skipped ones", () => {
    const out = run(
      report([
        {
          title: "pwa.spec.ts",
          suites: [
            {
              title: "nested",
              specs: [
                spec("the manifest parses cleanly", "e2e/day25/pwa.spec.ts", [
                  { status: "skipped" },
                ]),
                spec(
                  "the start URL opens offline",
                  "e2e/day25/pwa.spec.ts",
                  [
                    {
                      status: "failed",
                      message: "Error: expect(received).toBe(expected)\n  - 1\n  + 2\n",
                    },
                  ],
                  "mobile-360x640",
                ),
              ],
            },
          ],
        },
      ]),
    );

    expect(out).toContain(
      "::error file=e2e/day25/pwa.spec.ts::the start URL opens offline [mobile-360x640] failed",
    );
    expect(out).toContain("expect(received).toBe(expected)");
    expect(out).not.toContain("the manifest parses cleanly");
    expect(out).toContain("playwright-failures: 1 of 2 spec(s) failed");
  });

  it("caps a long error message so the annotation stays readable", () => {
    const long = Array.from({ length: 40 }, (_, i) => `line ${i}`).join("\n");
    const out = run(
      report([{ title: "s", specs: [spec("t", "f.ts", [{ status: "failed", message: long }])] }]),
    );

    expect(out).toContain("line 0");
    expect(out).toContain("line 7");
    expect(out).not.toContain("line 8");
    expect(out).not.toContain("line 39");
  });

  it("says so, and exits 0, when nothing failed", () => {
    const out = run(
      report([{ title: "s", specs: [spec("t", "f.ts", [{ status: "passed" }])] }]),
    );

    expect(out).toContain("playwright-failures: 1 spec(s), none failed");
    expect(out).not.toContain("::error");
  });

  it("exits 0 on a missing report, so a diagnostic never fails the job", () => {
    const path = join(dir, "absent.json");
    const out = execFileSync("node", [SCRIPT, path], { encoding: "utf8" });

    expect(out).toContain("absent.json not found");
  });

  it("exits 0 on a report that is not valid JSON", () => {
    const out = run("{ this is not json");

    expect(out).toContain("is not valid JSON");
  });

  it("exits 0 on a report with no suites at all", () => {
    const out = run(JSON.stringify({ config: {}, suites: [] }));

    expect(out).toContain("playwright-failures: 0 spec(s), none failed");
  });
});
