/**
 * The committed bundle is the bundle the compiler produces.
 *
 * `frontend/public/private/safety-bundle.json` is generated at build time from
 * `backend/app/content/**`. A generated file that is committed can go stale —
 * somebody edits a safety pattern, forgets to rebuild, and the browser ships
 * yesterday's rules. So the artifact is verified three ways here:
 *
 * 1. recompiling from the content on disk produces a byte-identical file
 *    (`node scripts/build-private-bundle.mjs --check`, the same gate CI runs);
 * 2. compiling twice produces the same bytes, i.e. the compile is deterministic —
 *    canonical key order, no timestamps, no host paths;
 * 3. the Unicode claim the bundle makes about case folding is re-checked
 *    exhaustively (`--verify-unicode`): every code point a browser lowercases is
 *    covered by the Python-derived override table.
 *
 * The Python side of the same contract — bundle content equals what the Python
 * engine compiles — is `backend/tests/safety/test_private_bundle.py`.
 */

import { execFileSync } from "node:child_process";

import { describe, expect, it } from "vitest";

import { BUNDLE_PATH, FRONTEND_ROOT, readText, sha256File } from "../testSupport/repoArtifacts";
import { BUNDLE_FORMAT, parseBundle, type PrivateBundle } from "../rules/bundleSchema";

const COMPILER = "scripts/build-private-bundle.mjs";

interface CheckReport {
  ok: boolean;
  reason: string;
  sha256: string;
  bytes: number;
  counts: Record<string, number>;
  duplicates_tolerated: { file: string; message: string }[];
}

function runCompiler(...args: string[]): string {
  return execFileSync(process.execPath, [COMPILER, ...args], {
    cwd: FRONTEND_ROOT,
    encoding: "utf8",
  });
}

function check(): CheckReport {
  return JSON.parse(runCompiler("--check", "--json")) as CheckReport;
}

const bundle: PrivateBundle = parseBundle(JSON.parse(readText(BUNDLE_PATH)) as unknown);

describe("the committed private bundle", () => {
  it("is byte-identical to a fresh compile of the content on disk", () => {
    const report = check();
    expect(report.ok, report.reason).toBe(true);
    expect(report.sha256).toBe(sha256File(BUNDLE_PATH));
    expect(report.bytes).toBeGreaterThan(100_000);
  });

  it("carries the rules, output rules, helplines and locales it claims to", () => {
    expect(bundle.format).toBe(BUNDLE_FORMAT);
    expect(bundle.engine.rules.length).toBeGreaterThan(200);
    expect(bundle.output_guard.rules.length).toBeGreaterThan(50);
    expect(bundle.crisis.helplines.resources.length).toBeGreaterThan(20);
    expect(Object.keys(bundle.crisis.locales).sort()).toEqual(["bn", "en", "hi"]);
    const counts = check().counts;
    expect(counts.rules).toBe(bundle.engine.rules.length);
    expect(counts.output_rules).toBe(bundle.output_guard.rules.length);
    expect(counts.helplines).toBe(bundle.crisis.helplines.resources.length);
  });

  it("names every source file it was compiled from, with a hash", () => {
    // Keys are relative to `backend/app/`, which is how
    // `backend/tests/safety/test_private_bundle.py` resolves them too.
    const sources = Object.keys(bundle.sources).sort();
    expect(sources).toContain("content/safety/patterns_core.yaml");
    expect(sources).toContain("content/helplines.json");
    expect(sources).toContain("content/i18n/en.json");
    for (const [name, entry] of Object.entries(bundle.sources)) {
      expect(entry.sha256, name).toMatch(/^[0-9a-f]{64}$/);
      expect(entry.bytes, name).toBeGreaterThan(0);
    }
  });

  it("records the duplicate YAML key it tolerated rather than hiding it", () => {
    // `patterns_context.yaml` repeats one misspellings key. PyYAML keeps the last
    // value, so the compiler is told to do the same (`json: true`) — and
    // `backend/tests/safety/test_private_bundle.py` asserts the two values are
    // identical, which is what makes the tolerance safe.
    const report = check();
    expect(report.duplicates_tolerated.length).toBeGreaterThan(0);
    expect(bundle.duplicates_tolerated).toEqual(report.duplicates_tolerated);
    expect(report.duplicates_tolerated[0]?.file).toContain("patterns_context.yaml");
  });

  it("compiles deterministically: two runs, same bytes", () => {
    const first = check().sha256;
    const second = check().sha256;
    expect(second).toBe(first);
  });

  it("covers every code point a browser would lowercase (exhaustive)", () => {
    // ~1.1M code points, once per verification run: the claim that Python's
    // casefold fixture is complete is only worth making if it is checked.
    const report = JSON.parse(runCompiler("--check", "--verify-unicode", "--json")) as CheckReport;
    expect(report.ok, report.reason).toBe(true);
  }, 60_000);
});
