/**
 * Prove the *production* Content-Security-Policy against the *built* bundle.
 *
 * The dev server has to relax `script-src` (`@vitejs/plugin-react` injects an
 * inline preamble), so the strict policy — `script-src 'self'`, no inline
 * script, no `eval` — is never exercised by a normal `npm run dev` session or
 * by the e2e suite. Unit tests only assert that the policy *text* says what we
 * think it says. That leaves the interesting question unanswered: does the
 * app the browser actually downloads still work when the policy is enforced?
 *
 * This script answers it, in a real browser, with no copies of the policy:
 *
 *   1. it reads the headers out of `docker/security-headers.conf` — the same
 *      file the nginx image includes — so it verifies the shipped policy and
 *      not a lookalike;
 *   2. it serves `dist/` with those headers and proxies `/api/*` to the
 *      backend, which is exactly the topology nginx provides in the container;
 *   3. it walks the app's routes in Chromium with a
 *      `securitypolicyviolation` listener installed, plus a console-error
 *      listener, and fails if *anything* is blocked.
 *
 * No Docker, no nginx binary and no network access required. It needs a built
 * `dist/` and a reachable API:
 *
 *   npm run build
 *   # API on :8000 with LLM_PROVIDER=fake (see playwright.config.ts)
 *   node scripts/verify-prod-csp.mjs
 *
 * Env: `API_URL` (default http://127.0.0.1:8000), `PORT` (default 4173),
 * `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH` / `PLAYWRIGHT_CHROMIUM_ARGS` are
 * honoured for restricted sandboxes, same as the e2e suite.
 */

import { createServer } from "node:http";
import { readFileSync, existsSync, statSync } from "node:fs";
import { extname, join, normalize, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = fileURLToPath(new URL(".", import.meta.url));
const frontendRoot = resolve(here, "..");
const repoRoot = resolve(frontendRoot, "..");

const DIST = join(frontendRoot, "dist");
const HEADERS_FILE = join(repoRoot, "docker", "security-headers.conf");
const API_URL = process.env.API_URL ?? "http://127.0.0.1:8000";
const PORT = Number(process.env.PORT ?? 4173);

const ROUTES = ["/", "/chat", "/journal", "/mood", "/settings"];

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".webp": "image/webp",
  ".woff2": "font/woff2",
  ".ico": "image/x-icon",
  ".map": "application/json; charset=utf-8",
  ".txt": "text/plain; charset=utf-8",
};

/**
 * Parse the nginx header file into a plain object.
 *
 * Only the two directives nginx uses here are understood — `add_header Name
 * "value" always;` — and an unrecognised directive is a hard error rather than
 * a silent skip: a header that quietly failed to parse would make this script
 * report a pass for a policy it never applied.
 */
function parseNginxHeaders(path) {
  const headers = {};
  for (const rawLine of readFileSync(path, "utf8").split("\n")) {
    const line = rawLine.trim();
    if (line === "" || line.startsWith("#")) continue;
    const match = /^add_header\s+([A-Za-z0-9-]+)\s+(.*?)\s+always;\s*$/.exec(line);
    if (!match) throw new Error(`unparsed line in ${path}: ${line}`);
    headers[match[1]] = match[2].replace(/^"(.*)"$/s, "$1");
  }
  if (!headers["Content-Security-Policy"]) {
    throw new Error(`no Content-Security-Policy in ${path}`);
  }
  return headers;
}

function serveStatic(req, res, headers) {
  const url = new URL(req.url, `http://localhost:${PORT}`);
  let pathname = decodeURIComponent(url.pathname);
  let filePath = join(DIST, normalize(pathname).replace(/^(\.\.[/\\])+/, ""));

  if (!existsSync(filePath) || statSync(filePath).isDirectory()) {
    // SPA fallback, exactly like `try_files $uri $uri/ /index.html` in nginx.
    filePath = join(DIST, "index.html");
  }
  const body = readFileSync(filePath);
  res.writeHead(200, {
    ...headers,
    "Content-Type": MIME[extname(filePath)] ?? "application/octet-stream",
    "Content-Length": body.length,
    // `Cache-Control: no-store` so a stale document cannot mask a violation.
    "Cache-Control": "no-store",
  });
  res.end(body);
}

async function proxyApi(req, res) {
  const target = new URL(req.url, API_URL);
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  const upstream = await fetch(target, {
    method: req.method,
    headers: {
      "content-type": req.headers["content-type"] ?? "application/json",
      accept: req.headers["accept"] ?? "*/*",
      authorization: req.headers["authorization"] ?? "",
      origin: `http://127.0.0.1:${PORT}`,
    },
    body: ["GET", "HEAD"].includes(req.method ?? "GET") ? undefined : Buffer.concat(chunks),
    redirect: "manual",
  });
  const headers = Object.fromEntries(upstream.headers.entries());
  // Hop-by-hop and length headers must not be copied verbatim: the body is
  // re-encoded, and a wrong Content-Length truncates an SSE stream.
  delete headers["content-length"];
  delete headers["content-encoding"];
  delete headers["transfer-encoding"];
  delete headers["connection"];
  res.writeHead(upstream.status, headers);
  const text = await upstream.text();
  res.end(text);
}

async function main() {
  if (!existsSync(join(DIST, "index.html"))) {
    console.error(`✗ no build found at ${DIST} — run \`npm run build\` first`);
    process.exit(2);
  }
  const headers = parseNginxHeaders(HEADERS_FILE);
  const csp = headers["Content-Security-Policy"];
  console.log(`policy under test:\n  ${csp}\n`);

  const server = createServer((req, res) => {
    const path = req.url ?? "/";
    if (path.startsWith("/api/")) {
      proxyApi(req, res).catch((error) => {
        res.writeHead(502, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ error: String(error) }));
      });
      return;
    }
    serveStatic(req, res, headers);
  });
  await new Promise((done) => server.listen(PORT, "127.0.0.1", done));
  console.log(`serving dist/ with the production headers on :${PORT}`);
  console.log(`proxying /api/ to ${API_URL}\n`);

  const { chromium } = await import("playwright");
  const browser = await chromium.launch({
    executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH,
    args: JSON.parse(process.env.PLAYWRIGHT_CHROMIUM_ARGS ?? "[]"),
  });
  const page = await browser.newPage({ viewport: { width: 360, height: 640 } });

  // A CSP violation does not throw and does not reject a promise: it fires an
  // event on the document and logs to the console. Both channels are captured,
  // and the listener is installed before the first navigation so nothing can
  // happen before it exists.
  await page.addInitScript(() => {
    window.__cspViolations = [];
    document.addEventListener("securitypolicyviolation", (event) => {
      window.__cspViolations.push(
        `${event.violatedDirective} blocked ${event.blockedURI || "(inline)"}` +
          ` at ${event.sourceFile || "?"}:${event.lineNumber}`,
      );
    });
  });
  const consoleErrors = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(`${message.text()}`);
  });
  page.on("pageerror", (error) => consoleErrors.push(`pageerror: ${error.message}`));

  let failed = false;
  for (const route of ROUTES) {
    consoleErrors.length = 0;
    const response = await page.goto(`http://127.0.0.1:${PORT}${route}`, {
      waitUntil: "networkidle",
    });
    const collected = await page.evaluate(() => window.__cspViolations ?? []);
    // Belt and braces: the app itself should be on screen, not an error page.
    const bodyText = await page.evaluate(() => document.body.innerText.slice(0, 200));
    const status = response?.status() ?? 0;
    const blocked = [...collected, ...consoleErrors];
    const ok = status === 200 && blocked.length === 0 && bodyText.trim() !== "";
    if (!ok) failed = true;
    console.log(
      `${ok ? "✓" : "✗"} ${route.padEnd(9)} HTTP ${status} · ` +
        `${blocked.length} blocked/console-error(s) · first text: ${JSON.stringify(
          bodyText.trim().split("\n")[0] ?? "",
        )}`,
    );
    for (const item of blocked) console.log(`    - ${item}`);
  }

  await browser.close();
  server.close();

  if (failed) {
    console.error("\n✗ the production CSP blocked something the app needs");
    process.exit(1);
  }
  console.log("\n✓ the built app runs under the production CSP with nothing blocked");
}

await main();
