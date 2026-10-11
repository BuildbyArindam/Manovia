import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { ESLint } from "eslint";
import { expect, it } from "vitest";

it("ignores generated browser artifacts without excluding runner source or e2e tests", async () => {
  const eslint = new ESLint({
    cwd: resolve(dirname(fileURLToPath(import.meta.url)), "..", "..", ".."),
  });
  for (const artifact of [
    "playwright-report/trace/runtime.js",
    "test-results/artifact.js",
    ".playwright/browser.js",
  ]) {
    expect(await eslint.isPathIgnored(artifact)).toBe(true);
  }
  for (const source of [
    "src/features/exercises/ExerciseRunner.tsx",
    "e2e/exercises.spec.ts",
    "eslint.config.js",
  ]) {
    expect(await eslint.isPathIgnored(source)).toBe(false);
  }
});
