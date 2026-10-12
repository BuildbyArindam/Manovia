/**
 * The Day 25 browser checks write findings to `test-results/day25/`, and the
 * verification document quotes them. Before this fix the axe spec appended to
 * whatever an earlier local run had left there, so a second run reported both
 * runs' scans. These tests pin the cleanup and its wiring (Day 25
 * verification): the function removes exactly the report files, the config
 * runs it once per invocation, and the specs write where it cleans.
 */

import { mkdtempSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { afterEach, describe, expect, it } from "vitest";

import { REPORT_DIR, REPORT_FILE, clearReports } from "../../e2e/day25/reports";

const FRONTEND = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const read = (relative: string): string => readFileSync(join(FRONTEND, relative), "utf8");
/** Code only: doc comments may name the folder in prose. */
const code = (source: string): string =>
  source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");

let dir: string | null = null;

afterEach(() => {
  if (dir !== null) {
    rmSync(dir, { recursive: true, force: true });
    dir = null;
  }
});

describe("clearReports", () => {
  it("removes the previous run's findings and nothing else", () => {
    dir = mkdtempSync(join(tmpdir(), "day25-reports-"));
    const stale = [
      "axe-mobile-360x640.jsonl",
      "axe-desktop-1280x800.jsonl",
      "axe-mobile-360x640.json",
      "keyboard-desktop-1280x800.json",
      "zoom-zoom-400-.json",
      "zoom-text-200-.json",
    ];
    for (const name of [...stale, "results.json", "notes.txt"]) {
      writeFileSync(join(dir, name), "{}\n");
    }
    mkdirSync(join(dir, "artifacts"));

    expect(clearReports(dir)).toEqual([...stale].sort());
    expect(readdirSync(dir).sort()).toEqual(["artifacts", "notes.txt", "results.json"]);
    // Idempotent: a second call finds nothing to do.
    expect(clearReports(dir)).toEqual([]);
  });

  it("creates the folder when it does not exist yet (first run on a clean checkout)", () => {
    dir = mkdtempSync(join(tmpdir(), "day25-reports-"));
    const target = join(dir, "nested", "day25");
    expect(clearReports(target)).toEqual([]);
    expect(readdirSync(target)).toEqual([]);
  });

  it("matches the names the specs write and not Playwright's own report", () => {
    for (const name of [
      "axe-mobile-360x640.jsonl",
      "keyboard-mobile-360x640.json",
      "zoom-zoom-200-.json",
    ]) {
      expect(name).toMatch(REPORT_FILE);
    }
    for (const name of ["results.json", "artifacts", "axe.txt", "keyboard.json"]) {
      expect(name).not.toMatch(REPORT_FILE);
    }
  });
});

describe("wiring", () => {
  it("the production Playwright config runs the cleanup once per invocation", () => {
    expect(read("playwright.prod.config.ts")).toContain(
      'globalSetup: "./e2e/day25/global-setup.ts"',
    );
    expect(read("e2e/day25/global-setup.ts")).toMatch(/clearReports\(\)/);
  });

  it("the dev-server config never runs the Day 25 specs (they need the production build)", () => {
    expect(read("playwright.config.ts")).toContain('testIgnore: ["day25/**"]');
    expect(read("playwright.prod.config.ts")).toContain('testDir: "./e2e/day25"');
  });

  it("every spec that writes findings writes them into the folder that is cleaned", () => {
    for (const spec of ["a11y", "keyboard", "zoom"]) {
      const source = code(read(`e2e/day25/${spec}.spec.ts`));
      expect(source, spec).toContain('import { REPORT_DIR } from "./reports";');
      expect(source, spec).toContain("${REPORT_DIR}/");
      expect(source, spec).not.toMatch(/test-results\/day25/);
    }
    expect(REPORT_DIR).toBe("test-results/day25");
  });
});
