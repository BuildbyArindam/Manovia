/**
 * Playwright configuration for the Day 12 chat e2e.
 *
 * Two projects, because the brief asks for both viewports to be *verified*, not
 * merely supported: 360x640 (the smallest phone this app targets) and
 * 1280x800. The same spec runs in both; only the viewport differs.
 *
 * The API is **not** started here on purpose. The e2e must run against the real
 * backend with `LLM_PROVIDER=fake` — the whole point is that the SSE contract
 * meets a real server, a real safety gate and a real stream — so the operator
 * brings the stack up (docker compose, or `make dev` in `backend/`) and this
 * config only starts the Vite dev server and points its `/api` proxy at it.
 * Override either side with `E2E_API_URL` / `E2E_PORT`.
 */

import { defineConfig, devices } from "@playwright/test";

const PORT = Number(process.env.E2E_PORT ?? 5173);
const API_URL = process.env.E2E_API_URL ?? "http://127.0.0.1:8000";
const BASE_URL = `http://127.0.0.1:${PORT}`;

export default defineConfig({
  testDir: "./e2e",
  /** One backend and one in-memory ephemeral store: serial keeps runs honest. */
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 60_000,
  expect: { timeout: 15_000 },
  reporter: [["list"], ["html", { open: "never", outputFolder: "playwright-report" }]],
  outputDir: "test-results",

  use: {
    baseURL: BASE_URL,
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
    command: `npm run dev -- --host 0.0.0.0 --port ${PORT} --strictPort`,
    url: BASE_URL,
    reuseExistingServer: true,
    timeout: 120_000,
    // The dev server proxies /api here; the browser itself only ever talks to
    // its own origin, which is what the sandbox preview requires.
    env: { VITE_API_PROXY_TARGET: API_URL },
  },
});
