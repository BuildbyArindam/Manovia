/**
 * The structural proof that Private Mode's core cannot reach the network.
 *
 * The Playwright test proves it behaviourally, by blocking every request and
 * completing a conversation. This proves it statically: the modules that decide
 * safety and write the transcript contain no network call at all, so there is no
 * code path that could leak message text — not one that is "currently unused", not
 * one behind a flag.
 *
 * The scan is over the safety-critical directories only. Three places legitimately
 * touch the network and are excluded from the strict scan, each for a stated reason
 * and each with its own assertions below:
 *
 *   bundle/load.ts             fetches the compiled safety bundle and the model
 *                              manifest from the same origin. Both are static assets
 *                              built from this repository; neither carries user data.
 *   models/web/*               the real runtimes: transformers.js and WebLLM download
 *                              models, after capability detection and explicit consent.
 *   session/usePrivateMode.ts  the browser composition root. It is the only core-side
 *                              module allowed to import `models/web/*`, because it is
 *                              where the download manager is wired to a fetcher. It
 *                              never issues a request itself and never touches the
 *                              API client.
 *
 * Everything else in `session/` — `privateSession.ts`, `usePrivateChat.ts` — is
 * scanned strictly: those are the modules that hold message text.
 */

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { FRONTEND_ROOT } from "../testSupport/repoArtifacts";

const PRIVATE_ROOT = join(FRONTEND_ROOT, "src", "features", "private");

/** Directories where a network call would be a privacy bug. */
const CORE_DIRS = ["rules", "crisis", "session", "store", "crypto"];

/** Anything that opens a connection, in source form. */
const NETWORK_PATTERNS: [string, RegExp][] = [
  ["fetch(", /\bfetch\s*\(/],
  ["XMLHttpRequest", /XMLHttpRequest/],
  ["WebSocket", /\bWebSocket\b/],
  ["EventSource", /\bEventSource\b/],
  ["sendBeacon", /sendBeacon/],
  ["importScripts", /importScripts/],
  ["axios", /\baxios\b/],
  ["an /api call", /["'`]\/?api\//],
  ["a dynamic import", /\bimport\s*\(/],
];

function sourceFiles(directory: string): string[] {
  const found: string[] = [];
  for (const entry of readdirSync(directory)) {
    const path = join(directory, entry);
    if (statSync(path).isDirectory()) {
      found.push(...sourceFiles(path));
      continue;
    }
    // Test files are excluded on purpose: they stub `fetch` to prove it is never
    // called, so the string has to appear in them.
    if ((entry.endsWith(".ts") || entry.endsWith(".tsx")) && !entry.endsWith(".test.ts")) {
      found.push(path);
    }
  }
  return found.sort();
}

/** Strip comments, so a docstring mentioning `fetch` is not a finding. */
function code(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/^\s*\/\/.*$/gm, "")
    .replace(/\s\/\/.*$/gm, "");
}

/**
 * Matches a *value* import of `module`. `verbatimModuleSyntax` is on, so
 * `import type { ... }` is the only form that can import a type without pulling in
 * code — which is what makes the distinction safe to rely on.
 */
function valueImport(module: string): RegExp {
  return new RegExp(`import\\s+(?!type\\b)[^;]*from\\s+"[^"]*${module}"`);
}

/** The one core-side module that wires the network runtimes, by design. */
const COMPOSITION_ROOT = join(PRIVATE_ROOT, "session", "usePrivateMode.ts");

describe("Private Mode's core has no network path", () => {
  const scanned = CORE_DIRS.flatMap((dir) => sourceFiles(join(PRIVATE_ROOT, dir)));
  const coreFiles = scanned.filter((file) => file !== COMPOSITION_ROOT);

  it("scans the modules that handle message text", () => {
    expect(coreFiles.length).toBeGreaterThan(10);
    // The composition root is excluded from the strict list, and only it is.
    expect(scanned).toContain(COMPOSITION_ROOT);
    expect(coreFiles).not.toContain(COMPOSITION_ROOT);
    for (const dir of CORE_DIRS) {
      expect(
        coreFiles.some((file) => file.includes(`${dir}/`)),
        `${dir} contributed no files`,
      ).toBe(true);
    }
  });

  it.each(coreFiles.map((file) => [file.replace(`${PRIVATE_ROOT}/`, ""), file]))(
    "%s makes no network call",
    (_label, file) => {
      const source = code(readFileSync(file, "utf8"));
      for (const [name, pattern] of NETWORK_PATTERNS) {
        expect(pattern.test(source), `${file} contains ${name}`).toBe(false);
      }
    },
  );

  it("imports nothing from the runtime downloaders", () => {
    for (const file of coreFiles) {
      const source = readFileSync(file, "utf8");
      expect(source, file).not.toMatch(/from\s+"[^"]*models\/web\//);
      // `@/lib/endpoints` is allowed: it is the wire *type* contract, no client.
      // `import type { ChatController } from "@/features/chat/useChat"` is allowed
      // for the same reason — `verbatimModuleSyntax` is on, so a type-only import
      // cannot pull in the module's code. What is banned is a *value* import of
      // anything that can issue a request.
      expect(source, file).not.toMatch(valueImport("lib\\/api"));
      expect(source, file).not.toMatch(valueImport("chatTransport"));
      expect(source, file).not.toMatch(valueImport("features\\/chat\\/useChat"));
    }
  });

  it("keeps the only same-origin loader honest about what it may fetch", () => {
    const loader = readFileSync(join(PRIVATE_ROOT, "bundle", "load.ts"), "utf8");
    const source = code(loader);
    // It may fetch, but only relative paths under /private/ — never an absolute
    // origin, and never a URL built from user input.
    expect(source).toMatch(/\bfetchImpl\s*\(/);
    expect(source).toMatch(/options\.fetchImpl \?\? fetch/);
    expect(source).not.toMatch(/https?:\/\//);
    expect(source).toMatch(/\/private\//);
    // Both URLs are literals, so neither can carry message text or a region.
    expect(loader).toMatch(/BUNDLE_URL = "\/private\/safety-bundle\.json"/);
    expect(loader).toMatch(/MANIFEST_URL = "\/private\/model-manifest\.json"/);
    expect(source).not.toMatch(/credentials:\s*"include"/);
  });

  it("lets the composition root wire the runtimes, and nothing else", () => {
    const source = code(readFileSync(COMPOSITION_ROOT, "utf8"));
    const raw = readFileSync(COMPOSITION_ROOT, "utf8");
    // It may import the browser adapters...
    expect(raw).toMatch(/from\s+"[^"]*models\/web\//);
    // ...but it issues no request of its own: it hands `fetchImpl` down to the
    // loader and the fetcher, so every byte it moves is a model file or a static
    // asset, chosen by those modules and never built from message text.
    expect(source).not.toMatch(/\bfetch\s*\(/);
    expect(source).not.toMatch(/XMLHttpRequest|WebSocket|EventSource|sendBeacon/);
    expect(source).not.toMatch(/credentials:\s*"include"/);
    expect(source).not.toMatch(/https?:\/\//);
    // And it is not a back door to the server pipeline.
    expect(raw).not.toMatch(/from\s+"[^"]*lib\/api"/);
    expect(raw).not.toMatch(/from\s+"[^"]*chatTransport"/);
    expect(raw).not.toMatch(/from\s+"[^"]*features\/chat\/useChat"/);
  });

  it("still fails on a value import of the API controller, and only on that", () => {
    // The rule above allows `import type`. This is the regression guard for the
    // allowance: it must not have become a hole.
    const pattern = valueImport("features\\/chat\\/useChat");
    expect('import { useChat } from "@/features/chat/useChat";').toMatch(pattern);
    expect('import { useChat, type ChatController } from "@/features/chat/useChat";').toMatch(
      pattern,
    );
    expect('import type { ChatController } from "@/features/chat/useChat";').not.toMatch(pattern);
    expect('import { chatReducer } from "@/features/chat/chatState";').not.toMatch(pattern);
  });

  it("keeps the private chat controller free of any network import", () => {
    const raw = readFileSync(join(PRIVATE_ROOT, "session", "usePrivateChat.ts"), "utf8");
    expect(raw).not.toMatch(/from\s+"[^"]*models\/web\//);
    expect(raw).not.toMatch(/from\s+"[^"]*bundle\/load"/);
    expect(code(raw)).not.toMatch(/\bfetch\s*\(/);
  });

  it("confines the model downloaders to models/web", () => {
    const webFiles = sourceFiles(join(PRIVATE_ROOT, "models", "web"));
    expect(webFiles.length).toBeGreaterThan(0);
    for (const file of sourceFiles(join(PRIVATE_ROOT, "models"))) {
      if (file.includes("/web/")) {
        continue;
      }
      const source = code(readFileSync(file, "utf8"));
      expect(/\bfetch\s*\(/.test(source), `${file} should not fetch`).toBe(false);
    }
  });
});
