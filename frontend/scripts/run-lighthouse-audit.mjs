#!/usr/bin/env node
/**
 * Day 25 Lighthouse Mobile + PWA + Bundle Size audit runner.
 *
 * Why a dedicated runner around Lighthouse + `lighthouserc.cjs`:
 * 1. Without a pre-seeded `manovia.onboarding.v1` record in `localStorage`,
 *    navigating to `/`, `/chat`, `/mood`, and `/exercises` would render the
 *    Onboarding Welcome gate four times instead of auditing the real Home,
 *    Chat, Mood, and Exercises pages.
 * 2. This runner provisions a real guest session against the running backend
 *    (`/api/v1/auth/guest` + `/api/v1/consent`), injects the session and
 *    onboarding record into the browser profile via CDP before each Lighthouse
 *    navigation, runs the Lighthouse mobile profile (`formFactor: "mobile"`)
 *    on `/`, `/chat`, `/mood`, and `/exercises`, verifies PWA installability +
 *    Service Worker control, reports bundle sizes (raw + gzip), and enforces
 *    the Day 25 budgets (`performance >= 90`, `accessibility >= 95`, `PWA pass`).
 */

import { createServer } from "node:http";
import { existsSync, mkdirSync, readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { extname, join, normalize, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { gzipSync } from "node:zlib";

import lighthouse from "lighthouse";
import * as chromeLauncher from "chrome-launcher";

const HERE = fileURLToPath(new URL(".", import.meta.url));
const FRONTEND_ROOT = resolve(HERE, "..");
const REPO_ROOT = resolve(FRONTEND_ROOT, "..");
const DIST = join(FRONTEND_ROOT, "dist");
const HEADERS_FILE = join(REPO_ROOT, "docker", "security-headers.conf");
const API_URL = process.env.API_URL ?? "http://127.0.0.1:8000";
const PORT = Number(process.env.PORT ?? 4173);
const BASE_URL = `http://127.0.0.1:${PORT}`;

const ROUTES = ["/", "/chat", "/mood", "/exercises"];

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".webmanifest": "application/manifest+json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".txt": "text/plain; charset=utf-8",
};

function parseNginxHeaders(path) {
  const headers = {};
  for (const rawLine of readFileSync(path, "utf8").split("\n")) {
    const line = rawLine.trim();
    if (line === "" || line.startsWith("#")) continue;
    const match = /^add_header\s+([A-Za-z0-9-]+)\s+(.*?)\s+always;\s*$/.exec(line);
    if (!match) continue;
    headers[match[1]] = match[2].replace(/^"(.*)"$/s, "$1");
  }
  return headers;
}

function serveStatic(req, res, secHeaders) {
  const url = new URL(req.url ?? "/", BASE_URL);
  const pathname = decodeURIComponent(url.pathname);
  let filePath = join(DIST, normalize(pathname).replace(/^(\.\.[/\\])+/, ""));

  if (!existsSync(filePath) || statSync(filePath).isDirectory()) {
    filePath = join(DIST, "index.html");
  }
  const rawBody = readFileSync(filePath);
  const ext = extname(filePath);
  const acceptEncoding = String(req.headers["accept-encoding"] ?? "");
  const compressible = [".html", ".js", ".mjs", ".css", ".json", ".webmanifest", ".svg"].includes(
    ext,
  );
  const useGzip = compressible && acceptEncoding.includes("gzip");
  const body = useGzip ? gzipSync(rawBody) : rawBody;

  const isAsset = pathname.startsWith("/assets/");
  res.writeHead(200, {
    ...secHeaders,
    "Content-Type": MIME[ext] ?? "application/octet-stream",
    ...(useGzip ? { "Content-Encoding": "gzip" } : {}),
    "Content-Length": body.length,
    "Cache-Control": isAsset ? "public, max-age=31536000, immutable" : "no-cache",
  });
  res.end(body);
}

async function proxyApi(req, res) {
  const target = new URL(req.url ?? "/", API_URL);
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  const upstream = await fetch(target, {
    method: req.method,
    headers: {
      "content-type": req.headers["content-type"] ?? "application/json",
      accept: req.headers["accept"] ?? "*/*",
      authorization: req.headers["authorization"] ?? "",
      origin: BASE_URL,
    },
    body: ["GET", "HEAD"].includes(req.method ?? "GET") ? undefined : Buffer.concat(chunks),
    redirect: "manual",
  });
  const headers = Object.fromEntries(upstream.headers.entries());
  delete headers["content-length"];
  delete headers["content-encoding"];
  delete headers["transfer-encoding"];
  delete headers["connection"];
  res.writeHead(upstream.status, headers);
  res.end(await upstream.text());
}

async function provisionGuestSession() {
  const guestRes = await fetch(`${API_URL}/api/v1/auth/guest`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  });
  if (!guestRes.ok) {
    throw new Error(`POST /auth/guest failed with ${guestRes.status}`);
  }
  const session = await guestRes.json();
  const reqRes = await fetch(`${API_URL}/api/v1/consent/requirements`);
  const reqBody = reqRes.ok ? await reqRes.json() : { documents: [] };
  const grants = ["terms", "privacy", "ai_disclosure"].map((kind) => {
    const doc = (reqBody.documents ?? []).find((d) => d.kind === kind);
    return { kind, version: doc?.version ?? "1.0", granted: true };
  });
  await fetch(`${API_URL}/api/v1/consent`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${session.access_token}`,
    },
    body: JSON.stringify({ grants }),
  });

  const onboardingRecord = {
    version: 1,
    completedAt: new Date().toISOString(),
    consents: {
      terms: true,
      privacy: true,
      ai_disclosure: true,
      store_chat: false,
    },
    authMode: "guest",
    user: {
      id: session.user.id,
      email: session.user.email,
      isAnonymous: session.user.is_anonymous,
    },
  };
  const tokenRecord = {
    accessToken: session.access_token,
    refreshToken: session.refresh_token,
    tokenType: "bearer",
    expiresAt: Date.now() + 900 * 1000,
  };
  return { onboardingRecord, tokenRecord };
}

function collectBundleSizes() {
  const assetsDir = join(DIST, "assets");
  const files = readdirSync(assetsDir).sort();
  const rows = [];
  for (const file of files) {
    const full = join(assetsDir, file);
    if (!statSync(full).isFile()) continue;
    const raw = readFileSync(full);
    const gz = gzipSync(raw);
    rows.push({
      file: `assets/${file}`,
      rawBytes: raw.length,
      gzipBytes: gz.length,
      rawKb: (raw.length / 1024).toFixed(2),
      gzipKb: (gz.length / 1024).toFixed(2),
    });
  }
  return rows;
}

async function main() {
  if (!existsSync(join(DIST, "index.html"))) {
    console.error("✗ dist/index.html missing — run `npm run build` first");
    process.exit(2);
  }

  const secHeaders = parseNginxHeaders(HEADERS_FILE);
  const server = createServer((req, res) => {
    if ((req.url ?? "/").startsWith("/api/")) {
      proxyApi(req, res).catch((err) => {
        res.writeHead(502, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ error: String(err) }));
      });
      return;
    }
    serveStatic(req, res, secHeaders);
  });
  await new Promise((done) => server.listen(PORT, "127.0.0.1", done));

  const { onboardingRecord, tokenRecord } = await provisionGuestSession();

  const chromePath =
    process.env.CHROME_PATH ?? process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH ?? undefined;
  const chrome = await chromeLauncher.launch({
    chromePath,
    chromeFlags: [
      "--headless=shell",
      "--no-sandbox",
      "--disable-setuid-sandbox",
      "--no-zygote",
      "--disable-dev-shm-usage",
    ],
  });

  const results = [];
  let failed = false;

  try {
    // Seed localStorage on the origin before Lighthouse clears/navigates with
    // disableStorageReset: true so the audited routes render the real app shell.
    const { chromium: pw } = await import("playwright");
    const browser = await pw.connectOverCDP(`http://127.0.0.1:${chrome.port}`);
    const context = browser.contexts()[0] ?? (await browser.newContext());
    const seedPage = context.pages()[0] ?? (await context.newPage());
    await seedPage.goto(BASE_URL, { waitUntil: "domcontentloaded" });
    await seedPage.evaluate(
      ({ onboarding, tokens }) => {
        window.localStorage.setItem("manovia.onboarding.v1", JSON.stringify(onboarding));
        window.localStorage.setItem("manovia.tokens.v1", JSON.stringify(tokens));
      },
      { onboarding: onboardingRecord, tokens: tokenRecord },
    );
    await seedPage.reload({ waitUntil: "networkidle" });

    // Verify PWA manifest + service worker registration on the live origin.
    const pwaCheck = await seedPage.evaluate(async () => {
      const manifestLink = document.querySelector('link[rel="manifest"]')?.getAttribute("href");
      const manifestRes = manifestLink ? await fetch(manifestLink) : null;
      const manifestJson = manifestRes?.ok ? await manifestRes.json() : null;
      const swReg =
        "serviceWorker" in navigator ? await navigator.serviceWorker.ready : null;
      return {
        manifestHref: manifestLink ?? null,
        manifestOk: Boolean(manifestJson && manifestJson.short_name === "Manovia"),
        iconsCount: manifestJson?.icons?.length ?? 0,
        display: manifestJson?.display ?? null,
        swActive: Boolean(swReg?.active),
        swScope: swReg?.scope ?? null,
      };
    });

    for (const route of ROUTES) {
      const url = `${BASE_URL}${route}`;
      const runnerResult = await lighthouse(url, {
        port: chrome.port,
        output: "json",
        logLevel: "error",
        formFactor: "mobile",
        throttlingMethod: "simulate",
        disableStorageReset: true,
        onlyCategories: ["performance", "accessibility", "best-practices", "seo"],
        screenEmulation: {
          mobile: true,
          width: 412,
          height: 823,
          deviceScaleFactor: 1.75,
          disabled: false,
        },
      });

      const lhr = runnerResult?.lhr;
      const scores = {
        route,
        performance: Math.round((lhr?.categories?.performance?.score ?? 0) * 100),
        accessibility: Math.round((lhr?.categories?.accessibility?.score ?? 0) * 100),
        bestPractices: Math.round((lhr?.categories?.["best-practices"]?.score ?? 0) * 100),
        seo: Math.round((lhr?.categories?.seo?.score ?? 0) * 100),
        fcpMs: Math.round(lhr?.audits?.["first-contentful-paint"]?.numericValue ?? 0),
        lcpMs: Math.round(lhr?.audits?.["largest-contentful-paint"]?.numericValue ?? 0),
        tbtMs: Math.round(lhr?.audits?.["total-blocking-time"]?.numericValue ?? 0),
        cls: Number((lhr?.audits?.["cumulative-layout-shift"]?.numericValue ?? 0).toFixed(3)),
        speedIndexMs: Math.round(lhr?.audits?.["speed-index"]?.numericValue ?? 0),
        interactiveMs: Math.round(lhr?.audits?.["interactive"]?.numericValue ?? 0),
      };
      results.push(scores);

      if (scores.performance < 90 || scores.accessibility < 95) {
        failed = true;
      }
    }

    // Measure route transition / load timings across all routes including lazy ones
    const allRoutes = [
      "/",
      "/chat",
      "/mood",
      "/exercises",
      "/journal",
      "/cbt",
      "/assessments",
      "/insights",
      "/settings",
      "/accessibility",
    ];
    const routeTimings = [];
    for (const r of allRoutes) {
      const t0 = performance.now();
      await seedPage.goto(`${BASE_URL}${r}`, { waitUntil: "networkidle" });
      await seedPage.locator("main h1, main h2").first().waitFor({ state: "visible" });
      const elapsedMs = Math.round(performance.now() - t0);
      routeTimings.push({ route: r, loadMs: elapsedMs });
    }

    const bundles = collectBundleSizes();
    const slowestRoute = [...routeTimings].sort((a, b) => b.loadMs - a.loadMs)[0];

    const outDir = join(FRONTEND_ROOT, ".lighthouseci");
    mkdirSync(outDir, { recursive: true });
    const summary = {
      generatedAt: new Date().toISOString(),
      profile: "mobile (Moto G Power 412x823, simulated slow 4G + 4x CPU slowdown)",
      pwa: {
        ...pwaCheck,
        pass: pwaCheck.manifestOk && pwaCheck.iconsCount >= 3 && pwaCheck.swActive,
      },
      scores: results,
      routeTimings,
      slowestRoute,
      bundles,
    };
    writeFileSync(join(outDir, "day25-summary.json"), JSON.stringify(summary, null, 2));

    console.log("\n=== Lighthouse Mobile Scores (target: Perf >= 90, A11y >= 95) ===");
    for (const row of results) {
      console.log(
        `  ${row.route.padEnd(12)} Perf=${row.performance}  A11y=${row.accessibility}  ` +
          `BestPractices=${row.bestPractices}  SEO=${row.seo}  ` +
          `(FCP=${row.fcpMs}ms, LCP=${row.lcpMs}ms, TBT=${row.tbtMs}ms, CLS=${row.cls})`,
      );
    }
    console.log(
      `\n=== PWA Check ===\n  pass=${summary.pwa.pass}  manifest=${summary.pwa.manifestHref}  ` +
        `display=${summary.pwa.display}  icons=${summary.pwa.iconsCount}  swActive=${summary.pwa.swActive}`,
    );
    console.log("\n=== Route Load Timings ===");
    for (const rt of routeTimings) {
      console.log(`  ${rt.route.padEnd(16)} ${rt.loadMs} ms`);
    }
    console.log(
      `  Slowest route: ${slowestRoute?.route} (${slowestRoute?.loadMs} ms)`,
    );
    console.log("\n=== Bundle Sizes (dist/assets) ===");
    for (const b of bundles) {
      console.log(
        `  ${b.file.padEnd(42)} raw=${b.rawKb.padStart(7)} kB   gzip=${b.gzipKb.padStart(7)} kB`,
      );
    }

    if (!summary.pwa.pass) {
      failed = true;
    }
  } finally {
    await chrome.kill();
    server.close();
  }

  if (failed) {
    console.error("\n✗ Lighthouse / PWA budget check failed");
    process.exit(1);
  }
  console.log("\n✓ All Lighthouse mobile and PWA budgets passed");
}

await main();
