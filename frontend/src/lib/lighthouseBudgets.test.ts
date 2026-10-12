/**
 * The Lighthouse budget evaluator (scripts/lighthouse-ci.mjs) is our own code
 * since `@lhci/cli` was dropped (Day 25), so it gets its own proof that it can
 * fail: a gate that cannot fail is not a gate.
 */

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

// @ts-expect-error -- a plain .mjs operator script, no type declarations
import { assertBudgets } from "../../scripts/lighthouse-ci.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const { assertions } = JSON.parse(readFileSync(join(HERE, "../../lighthouserc.json"), "utf8")).ci
  .assert as { assertions: Record<string, [string, Record<string, number>]> };

function lhr(overrides: {
  performance?: number;
  accessibility?: number;
  seo?: number;
  cls?: number;
  scriptBytes?: number;
  thirdParty?: number;
}) {
  return {
    categories: {
      performance: { score: overrides.performance ?? 0.98 },
      accessibility: { score: overrides.accessibility ?? 1 },
      "best-practices": { score: 1 },
      seo: { score: overrides.seo ?? 1 },
    },
    audits: {
      "cumulative-layout-shift": { numericValue: overrides.cls ?? 0 },
      "largest-contentful-paint": { numericValue: 1800 },
      "total-blocking-time": { numericValue: 20 },
      "resource-summary": {
        details: {
          items: [
            {
              resourceType: "script",
              transferSize: overrides.scriptBytes ?? 120_000,
              requestCount: 4,
            },
            {
              resourceType: "third-party",
              transferSize: 0,
              requestCount: overrides.thirdParty ?? 0,
            },
            { resourceType: "total", transferSize: 160_000, requestCount: 12 },
          ],
        },
      },
    },
  };
}

type Result = { failures: string[]; warnings: string[] };
const run = (overrides: Parameters<typeof lhr>[0]): Result =>
  assertBudgets([{ route: "/mood", lhr: lhr(overrides) }], assertions) as Result;

describe("Lighthouse budgets", () => {
  it("passes a result inside every budget", () => {
    expect(run({})).toEqual({ failures: [], warnings: [] });
  });

  it("fails performance under 90 and accessibility under 95", () => {
    const { failures } = run({ performance: 0.89, accessibility: 0.94 });
    expect(failures).toHaveLength(2);
    expect(failures[0]).toContain("categories:performance");
    expect(failures[1]).toContain("categories:accessibility");
  });

  it("fails layout shift, oversized scripts and any third-party request", () => {
    const { failures } = run({ cls: 0.2, scriptBytes: 250_000, thirdParty: 1 });
    expect(failures.map((line) => line.split("  ")[1]?.split(":")[0])).toEqual([
      "cumulative-layout-shift",
      "resource-summary",
      "resource-summary",
    ]);
  });

  it("only warns for the warn-level budgets", () => {
    expect(run({ seo: 0.5 })).toEqual({
      failures: [],
      warnings: [expect.stringContaining("categories:seo")],
    });
  });
});
