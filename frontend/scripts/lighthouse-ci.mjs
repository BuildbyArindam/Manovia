#!/usr/bin/env node
/**
 * Lighthouse CI for Manovia (Day 25): mobile profile, real signed-in pages.
 *
 * Why a collector of our own instead of `lhci collect`: every route except the
 * onboarding gate needs a signed-in, consented session, and Lighthouse resets
 * storage before each run by default — which would audit the onboarding screen
 * four times. This script:
 *
 *   1. launches Chrome (chrome-launcher, the same launcher LHCI uses);
 *   2. creates a real guest session through the real API and stores it the way
 *      the app does (tokens + onboarding record in localStorage);
 *   3. before *each* run clears the service worker, Cache Storage and the HTTP
 *      cache — but not localStorage — so every audit is a cold first load of a
 *      signed-in page, not a warm load served by the worker;
 *   4. runs Lighthouse 13 (mobile form factor, simulated slow-4G + 4x CPU
 *      throttling: the defaults) with `disableStorageReset` so the session
 *      survives;
 *   5. writes each result (JSON + HTML) to `lighthouse-reports/` and to
 *      `.lighthouseci/lhr-*.json` (the LHCI layout), prints the four category
 *      scores per route, and asserts the budgets in `lighthouserc.json`.
 *
 * The budgets file uses the LHCI assertion format, so `lhci assert` can read
 * it too — but the `@lhci/cli` package is not a dependency: it pins
 * Lighthouse 12 and an old puppeteer whose transitive packages carried eleven
 * high-severity advisories. Lighthouse 13 + puppeteer-core 25 carry none, so
 * this script evaluates the same assertions itself (see `assertBudgets`).
 *
 * Lighthouse dropped its PWA category in v12; installability is asserted in a
 * real browser by `e2e/day25/pwa.spec.ts`. The "agentic-browsing" category
 * added in v13 is not part of the four scores and is not run.
 *
 * Env: BASE_URL (default http://127.0.0.1:4173), LH_ROUTES (comma list,
 * default "/,/chat,/mood,/exercises"), LH_RUNS (per route, default 1; the
 * median-performance run is kept), CHROME_PATH (or the Playwright sandbox
 * variable PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH).
 */

import { mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import * as chromeLauncher from "chrome-launcher";
import lighthouse from "lighthouse";
import puppeteer from "puppeteer-core";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const BASE_URL = (process.env.BASE_URL ?? "http://127.0.0.1:4173").replace(/\/$/, "");
const ROUTES = (process.env.LH_ROUTES ?? "/,/chat,/mood,/exercises")
  .split(",")
  .map((r) => r.trim());
const RUNS = Math.max(1, Number(process.env.LH_RUNS ?? 1));
const CHROME_PATH = process.env.CHROME_PATH ?? process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH;
const LHCI_DIR = join(ROOT, ".lighthouseci");
const REPORT_DIR = join(ROOT, "lighthouse-reports");

const CATEGORIES = ["performance", "accessibility", "best-practices", "seo"];

/** Create a consented guest in the page's own origin, exactly like onboarding does. */
async function seedSession(browser) {
  const page = await browser.newPage();
  await page.goto(`${BASE_URL}/`, { waitUntil: "load" });
  const seeded = await page.evaluate(async () => {
    const json = async (response) => {
      if (!response.ok) throw new Error(`${response.url} -> ${response.status}`);
      return response.json();
    };
    const session = await json(await fetch("/api/v1/auth/guest", { method: "POST" }));
    const requirements = await json(await fetch("/api/v1/consent/requirements"));
    const version = (kind) =>
      requirements.documents.find((document) => document.kind === kind)?.version ?? "2026-10-01";
    const grants = ["terms", "privacy", "ai_disclosure"].map((kind) => ({
      kind,
      version: version(kind),
      granted: true,
    }));
    await json(
      await fetch("/api/v1/consent", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${session.access_token}`,
        },
        body: JSON.stringify({ grants }),
      }),
    );
    localStorage.setItem(
      "manovia.tokens.v1",
      JSON.stringify({
        accessToken: session.access_token,
        refreshToken: session.refresh_token,
        tokenType: "bearer",
        expiresAt: Date.now() + session.expires_in * 1000,
      }),
    );
    localStorage.setItem(
      "manovia.onboarding.v1",
      JSON.stringify({
        version: 1,
        completedAt: new Date().toISOString(),
        consents: { terms: true, privacy: true, ai_disclosure: true, store_chat: false },
        authMode: "guest",
        user: { id: session.user.id, email: null, isAnonymous: true },
      }),
    );
    return session.user.id;
  });
  await page.close();
  return seeded;
}

/** Cold start for the next audit: no worker, no Cache Storage, no HTTP cache. */
async function coldStart(browser) {
  const page = await browser.newPage();
  await page.goto(`${BASE_URL}/manifest.webmanifest`);
  const cdp = await page.createCDPSession();
  await cdp.send("Storage.clearDataForOrigin", {
    origin: BASE_URL,
    storageTypes: "service_workers,cache_storage",
  });
  await cdp.send("Network.clearBrowserCache");
  await page.close();
}

/**
 * Evaluate LHCI-format assertions against the kept results:
 *   "categories:<id>": [level, { minScore }]
 *   "resource-summary:<type>:size|count": [level, { maxNumericValue }]
 *   "<audit-id>": [level, { maxNumericValue } | { minScore }]
 * `error` fails the run; `warn` is printed and does not.
 */
export function assertBudgets(results, assertions) {
  const failures = [];
  const warnings = [];
  for (const { route, lhr } of results) {
    for (const [key, [level, options]] of Object.entries(assertions)) {
      let actual;
      let ok;
      let expected;
      if (key.startsWith("categories:")) {
        actual = lhr.categories[key.slice("categories:".length)]?.score ?? 0;
        ok = actual >= options.minScore;
        expected = `>= ${options.minScore}`;
      } else if (key.startsWith("resource-summary:")) {
        const [, type, measure] = key.split(":");
        const item = lhr.audits["resource-summary"].details.items.find(
          (entry) => entry.resourceType === type,
        );
        actual = measure === "count" ? (item?.requestCount ?? 0) : (item?.transferSize ?? 0);
        ok = actual <= options.maxNumericValue;
        expected = `<= ${options.maxNumericValue}`;
      } else {
        const audit = lhr.audits[key];
        if (options.maxNumericValue !== undefined) {
          actual = audit?.numericValue ?? Number.POSITIVE_INFINITY;
          ok = actual <= options.maxNumericValue;
          expected = `<= ${options.maxNumericValue}`;
        } else {
          actual = audit?.score ?? 0;
          ok = actual >= options.minScore;
          expected = `>= ${options.minScore}`;
        }
      }
      if (!ok) {
        const line = `${route}  ${key}: ${typeof actual === "number" ? Number(actual.toFixed(3)) : actual} (expected ${expected})`;
        (level === "error" ? failures : warnings).push(line);
      }
    }
  }
  return { failures, warnings };
}

function median(values) {
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.floor(sorted.length / 2)];
}

async function main() {
  rmSync(LHCI_DIR, { recursive: true, force: true });
  mkdirSync(LHCI_DIR, { recursive: true });
  mkdirSync(REPORT_DIR, { recursive: true });

  const chrome = await chromeLauncher.launch({
    chromePath: CHROME_PATH,
    chromeFlags: ["--headless=new", "--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
  });
  const browser = await puppeteer.connect({ browserURL: `http://127.0.0.1:${chrome.port}` });
  const rows = [];
  const kept = [];
  try {
    const user = await seedSession(browser);
    console.log(`seeded guest session ${user.slice(0, 8)}… for ${BASE_URL}`);

    for (const route of ROUTES) {
      const runs = [];
      for (let run = 0; run < RUNS; run += 1) {
        await coldStart(browser);
        const result = await lighthouse(
          `${BASE_URL}${route}`,
          {
            port: chrome.port,
            output: ["json", "html"],
            logLevel: "error",
            disableStorageReset: true,
          },
          {
            extends: "lighthouse:default",
            settings: { formFactor: "mobile", onlyCategories: CATEGORIES },
          },
        );
        runs.push(result);
      }
      const chosen = runs.find(
        (result) =>
          result.lhr.categories.performance.score ===
          median(runs.map((r) => r.lhr.categories.performance.score)),
      );
      const [json, html] = chosen.report;
      const stamp = `${Date.now()}`;
      writeFileSync(join(LHCI_DIR, `lhr-${stamp}.json`), json);
      const slug = route === "/" ? "home" : route.replace(/\W+/g, "");
      writeFileSync(join(REPORT_DIR, `${slug}.report.html`), html);
      writeFileSync(join(REPORT_DIR, `${slug}.report.json`), json);
      const lhr = chosen.lhr;
      kept.push({ route, lhr });
      rows.push({
        route,
        ...Object.fromEntries(
          CATEGORIES.map((category) => [
            category,
            Math.round((lhr.categories[category]?.score ?? 0) * 100),
          ]),
        ),
        lcp: lhr.audits["largest-contentful-paint"].displayValue,
        tbt: lhr.audits["total-blocking-time"].displayValue,
        cls: lhr.audits["cumulative-layout-shift"].displayValue,
        finalUrl: lhr.finalDisplayedUrl,
      });
    }
  } finally {
    await browser.disconnect();
    await chrome.kill();
  }

  console.log("\nroute         perf  a11y  best-practices  seo   LCP       TBT     CLS");
  for (const row of rows) {
    console.log(
      `${row.route.padEnd(12)}  ${String(row.performance).padStart(4)}  ${String(row.accessibility).padStart(4)}  ${String(row["best-practices"]).padStart(14)}  ${String(row.seo).padStart(4)}  ${row.lcp.padEnd(8)}  ${row.tbt.padEnd(6)}  ${row.cls}`,
    );
    if (!row.finalUrl.endsWith(row.route)) {
      console.log(`  ! ${row.route} ended at ${row.finalUrl}`);
    }
  }
  const { assertions } = JSON.parse(readFileSync(join(ROOT, "lighthouserc.json"), "utf8")).ci
    .assert;
  const { failures, warnings } = assertBudgets(kept, assertions);
  writeFileSync(
    join(REPORT_DIR, "summary.json"),
    JSON.stringify(
      { lighthouse: kept[0]?.lhr.lighthouseVersion, rows, failures, warnings },
      null,
      2,
    ),
  );
  console.log(
    `\nLighthouse ${kept[0]?.lhr.lighthouseVersion}, mobile, ${RUNS} run(s) per route (median kept)`,
  );
  for (const warning of warnings) console.log(`warn  ${warning}`);
  for (const failure of failures) console.log(`FAIL  ${failure}`);
  console.log(
    failures.length === 0 ? "All Lighthouse budgets met." : `${failures.length} budget(s) failed.`,
  );
  process.exit(failures.length === 0 ? 0 : 1);
}

// Run only as a script, so the tests can import `assertBudgets`.
if (process.argv[1] !== undefined && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((error) => {
    console.error(error);
    process.exit(1);
  });
}
