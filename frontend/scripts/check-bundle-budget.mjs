#!/usr/bin/env node
/**
 * Bundle budgets for low-end phones on weak networks (Day 25).
 *
 * Reads `dist/.vite/manifest.json` (written by `vite build`, see
 * `build.manifest` in vite.config.ts) and follows the real import graph:
 *
 * * **initial**: the entry module plus everything it imports statically — what
 *   every first visit downloads and parses before anything renders;
 * * **per route**: what opening each lazily loaded page adds on top;
 * * **precache**: what the service worker downloads in the background after the
 *   first visit (every file it will store for offline use);
 * * **slowest route**: initial + that route's increment, with a transfer-time
 *   estimate on Lighthouse's mobile profile (1.6 Mbps, 150 ms RTT).
 *
 * Sizes are gzip (zlib level 9, as nginx's `gzip_comp_level 9` would send) and
 * raw. Exits non-zero when a budget is exceeded, so CI fails on regressions.
 *
 *   npm run build && npm run budget          # human table
 *   node scripts/check-bundle-budget.mjs --json
 */

import { readFileSync, existsSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { gzipSync } from "node:zlib";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const DIST = join(ROOT, "dist");
const MANIFEST = join(DIST, ".vite", "manifest.json");

/** Budgets in bytes, gzip unless named raw. */
export const BUDGETS = {
  initialJsGzip: 130 * 1024,
  initialCssGzip: 10 * 1024,
  routeIncrementGzip: 25 * 1024,
  /** Insights carries the chart library; it is lazy and not precached. */
  insightsIncrementGzip: 125 * 1024,
  precacheRaw: 800 * 1024,
};

/** Lighthouse "slow 4G" (mobile) network model. */
const NETWORK = { kbps: 1638.4, rttMs: 150 };

const ROUTES = {
  "src/pages/ChatPage.tsx": "/chat",
  "src/pages/MoodPage.tsx": "/mood",
  "src/pages/JournalPage.tsx": "/journal",
  "src/pages/CBTPage.tsx": "/cbt",
  "src/pages/AssessmentsPage.tsx": "/assessments",
  "src/pages/ExercisesPage.tsx": "/exercises",
  "src/pages/InsightsPage.tsx": "/insights",
  "src/pages/SettingsPage.tsx": "/settings",
  "src/pages/AccessibilityPage.tsx": "/accessibility",
};

function sizes(file) {
  const bytes = readFileSync(join(DIST, file));
  return { raw: bytes.length, gzip: gzipSync(bytes, { level: 9 }).length };
}

function closure(manifest, key, seen = new Set()) {
  if (seen.has(key)) return seen;
  seen.add(key);
  for (const next of manifest[key]?.imports ?? []) {
    closure(manifest, next, seen);
  }
  return seen;
}

function filesOf(manifest, keys) {
  const js = new Set();
  const css = new Set();
  for (const key of keys) {
    const chunk = manifest[key];
    if (chunk === undefined) continue;
    js.add(chunk.file);
    for (const sheet of chunk.css ?? []) css.add(sheet);
  }
  return { js: [...js], css: [...css] };
}

function total(files) {
  return files.reduce(
    (sum, file) => {
      const size = sizes(file);
      return { raw: sum.raw + size.raw, gzip: sum.gzip + size.gzip };
    },
    { raw: 0, gzip: 0 },
  );
}

/** Seconds to fetch `bytes` after the HTML: one round trip per dependency wave. */
function transferSeconds(bytes, waves) {
  return (waves * NETWORK.rttMs) / 1000 + (bytes * 8) / (NETWORK.kbps * 1000);
}

function precacheFiles() {
  const sw = readFileSync(join(DIST, "sw.js"), "utf8");
  return [...sw.matchAll(/"url":"([^"]+)"/g)].map((match) => match[1]);
}

function kb(bytes) {
  return `${(bytes / 1024).toFixed(1)} KB`;
}

function main() {
  if (!existsSync(MANIFEST)) {
    console.error("No dist/.vite/manifest.json — run `npm run build` first.");
    process.exit(2);
  }
  const manifest = JSON.parse(readFileSync(MANIFEST, "utf8"));
  const entryKey = Object.keys(manifest).find((key) => manifest[key].isEntry);
  const initialKeys = closure(manifest, entryKey);
  const initial = filesOf(manifest, initialKeys);
  const initialJs = total(initial.js);
  const initialCss = total(initial.css);

  const routes = Object.entries(ROUTES).map(([key, path]) => {
    const keys = [...closure(manifest, key)].filter((k) => !initialKeys.has(k));
    const files = filesOf(manifest, keys);
    const size = total([...files.js, ...files.css]);
    const firstVisitBytes = initialJs.gzip + initialCss.gzip + size.gzip;
    return {
      path,
      chunks: files.js.length,
      raw: size.raw,
      gzip: size.gzip,
      firstVisitGzip: firstVisitBytes,
      // HTML -> entry wave -> route chunk wave
      estimatedSeconds: transferSeconds(firstVisitBytes, 3),
    };
  });
  const home = {
    path: "/",
    chunks: 0,
    raw: 0,
    gzip: 0,
    firstVisitGzip: initialJs.gzip + initialCss.gzip,
    estimatedSeconds: transferSeconds(initialJs.gzip + initialCss.gzip, 2),
  };
  const allRoutes = [home, ...routes];
  const slowest = allRoutes.reduce((a, b) => (b.estimatedSeconds > a.estimatedSeconds ? b : a));

  const precache = precacheFiles().filter((file) => existsSync(join(DIST, file)));
  const precacheSize = total(precache);

  const chunks = Object.values(manifest)
    .filter((chunk) => chunk.file.endsWith(".js"))
    .map((chunk) => ({ file: chunk.file, ...sizes(chunk.file) }))
    .sort((a, b) => b.gzip - a.gzip);

  const failures = [];
  const check = (label, actual, budget) => {
    if (actual > budget) failures.push(`${label}: ${kb(actual)} > budget ${kb(budget)}`);
  };
  check("initial JS (gzip)", initialJs.gzip, BUDGETS.initialJsGzip);
  check("initial CSS (gzip)", initialCss.gzip, BUDGETS.initialCssGzip);
  for (const route of routes) {
    const budget =
      route.path === "/insights" ? BUDGETS.insightsIncrementGzip : BUDGETS.routeIncrementGzip;
    check(`route ${route.path} increment (gzip)`, route.gzip, budget);
  }
  check("precache (raw)", precacheSize.raw, BUDGETS.precacheRaw);

  const report = {
    initial: { js: initialJs, css: initialCss, files: initial.js.length + initial.css.length },
    routes: allRoutes,
    slowest,
    precache: { files: precache.length, ...precacheSize },
    chunks,
    budgets: BUDGETS,
    failures,
  };

  if (process.argv.includes("--json")) {
    console.log(JSON.stringify(report, null, 2));
  } else {
    console.log("Bundle sizes (gzip level 9 / raw)\n");
    console.log(
      `initial JS   ${kb(initialJs.gzip).padStart(10)} / ${kb(initialJs.raw).padStart(10)}  (${initial.js.length} files)`,
    );
    console.log(
      `initial CSS  ${kb(initialCss.gzip).padStart(10)} / ${kb(initialCss.raw).padStart(10)}`,
    );
    console.log(
      `precache     ${"".padStart(10)}   ${kb(precacheSize.raw).padStart(10)}  (${precache.length} files, after first visit)\n`,
    );
    console.log("route          adds (gzip)   first visit (gzip)   est. transfer on slow 4G");
    for (const route of allRoutes) {
      console.log(
        `${route.path.padEnd(14)} ${kb(route.gzip).padStart(10)}   ${kb(route.firstVisitGzip).padStart(18)}   ${route.estimatedSeconds.toFixed(2).padStart(8)} s`,
      );
    }
    console.log(
      `\nslowest route: ${slowest.path} (${kb(slowest.firstVisitGzip)}, ~${slowest.estimatedSeconds.toFixed(2)} s)\n`,
    );
    console.log("largest chunks:");
    for (const chunk of chunks.slice(0, 8)) {
      console.log(
        `  ${chunk.file.padEnd(48)} ${kb(chunk.gzip).padStart(10)} / ${kb(chunk.raw).padStart(10)}`,
      );
    }
    console.log(
      failures.length === 0
        ? "\nAll bundle budgets met."
        : `\nOVER BUDGET:\n  ${failures.join("\n  ")}`,
    );
  }
  process.exit(failures.length === 0 ? 0 : 1);
}

main();
