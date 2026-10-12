/**
 * Playwright configuration for the Day 25 checks, which need the *production*
 * build: the service worker only exists in `vite build` output, so offline
 * behaviour, installability and real bundle performance cannot be tested
 * against the dev server (`playwright.config.ts` covers the Day 12–23 specs
 * there and ignores `e2e/day25`).
 *
 * The API is not started here, for the same reason as the dev config: the
 * specs must meet a real FastAPI backend with `LLM_PROVIDER=fake`. Bring it up
 * first (see frontend/README.md), then:
 *
 *   npm run test:e2e:prod
 *
 * `E2E_API_URL` / `E2E_PROD_PORT` override the defaults; the sandbox Chromium
 * variables are honoured exactly as in `playwright.config.ts`.
 */

import { defineConfig, devices } from "@playwright/test";

const PORT = Number(process.env.E2E_PROD_PORT ?? 4173);
const API_URL = process.env.E2E_API_URL ?? "http://127.0.0.1:8000";
const BASE_URL = `http://127.0.0.1:${PORT}`;
const chromiumArgs: unknown = JSON.parse(process.env.PLAYWRIGHT_CHROMIUM_ARGS ?? "[]");
if (!Array.isArray(chromiumArgs) || !chromiumArgs.every((arg) => typeof arg === "string")) {
  throw new Error("PLAYWRIGHT_CHROMIUM_ARGS must be a JSON array of strings");
}

export default defineConfig({
  testDir: "./e2e/day25",
  // Empties test-results/day25/ once per run, so its reports never mix runs.
  globalSetup: "./e2e/day25/global-setup.ts",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 120_000,
  expect: { timeout: 15_000 },
  reporter: [
    ["list"],
    ["json", { outputFile: "test-results/day25/results.json" }],
    ["html", { open: "never", outputFolder: "playwright-report/day25" }],
  ],
  outputDir: "test-results/day25/artifacts",

  use: {
    baseURL: BASE_URL,
    launchOptions: {
      executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH,
      args: chromiumArgs,
    },
    serviceWorkers: "allow",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "off",
  },

  projects: [
    {
      name: "mobile-360x640",
      use: { ...devices["Desktop Chrome"], viewport: { width: 360, height: 640 } },
    },
    {
      name: "desktop-1280x800",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 800 } },
    },
  ],

  webServer: {
    command: `npm run build && npx vite preview --host 0.0.0.0 --port ${PORT} --strictPort`,
    url: BASE_URL,
    reuseExistingServer: true,
    timeout: 240_000,
    env: { VITE_API_PROXY_TARGET: API_URL },
  },
});
