/**
 * The Content-Security-Policy, asserted against the files that actually ship.
 *
 * A strict policy is only worth having if nothing in the app needs the
 * relaxation it forbids. The thing that would break `script-src 'self'` is an
 * inline `<script>` or an inline event handler in `index.html`, and the thing
 * that would break `object-src 'none'` is an embed. This test reads the real
 * files — the document the build copies verbatim, the nginx snippet the web
 * container serves, and the Vite config the dev server uses — so the policy and
 * the markup cannot drift apart silently.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const read = (relative: string): string =>
  readFileSync(fileURLToPath(new URL(relative, import.meta.url)), "utf-8");

const INDEX_HTML = read("../../index.html");
const HEADERS_CONF = read("../../../docker/security-headers.conf");
const NGINX_CONF = read("../../../docker/nginx.conf");
const VITE_CONFIG = read("../../vite.config.ts");

/** The CSP directives of an `add_header Content-Security-Policy` line. */
function directivesOf(header: string): Map<string, string> {
  const inner = /Content-Security-Policy "([^"]+)"/.exec(header);
  const policy: string | undefined = inner?.[1];
  if (policy === undefined) {
    throw new Error(`no CSP found in: ${header.slice(0, 120)}`);
  }
  const map = new Map<string, string>();
  for (const part of policy.split(";")) {
    const [name, ...rest] = part.trim().split(/\s+/);
    if (name !== undefined && name !== "") {
      map.set(name, rest.join(" "));
    }
  }
  return map;
}

describe("index.html is compatible with script-src 'self'", () => {
  it("carries no inline script", () => {
    const scripts = [...INDEX_HTML.matchAll(/<script\b[^>]*>/g)].map((match) => match[0]);
    expect(scripts.length).toBeGreaterThan(0);
    for (const tag of scripts) {
      expect(tag).toContain("src=");
    }
    // And nothing at all between the tags of a script without a src.
    expect(INDEX_HTML).not.toMatch(/<script(?![^>]*\bsrc=)[^>]*>[\s\S]*?<\/script>/);
  });

  it("declares no inline event handler", () => {
    // `on…=` as an attribute (onerror=, onclick=, onload=) is inline script.
    expect(INDEX_HTML).not.toMatch(/\son[a-z]+\s*=\s*["']/i);
  });

  it("has no javascript: URL and no embed", () => {
    expect(INDEX_HTML).not.toMatch(/javascript:/i);
    expect(INDEX_HTML).not.toMatch(/<(iframe|object|embed)\b/i);
  });

  it("keeps the palette CSS fallback that makes the deferred theme module safe", () => {
    const css = read("../index.css");
    expect(css).toContain("prefers-color-scheme: dark");
    expect(css).toContain(':root:not([data-theme="light"])');
  });
});

describe("the shipped policy", () => {
  it("is strict about scripts and forbids plugins and framing", () => {
    const directives = directivesOf(HEADERS_CONF);
    expect(directives.get("script-src")).toBe("'self'");
    expect(directives.get("object-src")).toBe("'none'");
    expect(directives.get("frame-ancestors")).toBe("'none'");
    expect(directives.get("base-uri")).toBe("'self'");
    expect(directives.get("form-action")).toBe("'self'");
    expect(directives.get("default-src")).toBe("'self'");
    expect(directives.get("connect-src")).toBe("'self'");
  });

  it("relaxes exactly two things, and says so", () => {
    const directives = directivesOf(HEADERS_CONF);
    // Relaxation one: inline *styles* (React sets element styles).
    // Relaxation two (Day 23): the two Google Fonts origins, so Hindi and
    // Bengali get Noto Sans Devanagari / Bengali. Each is pinned to its exact
    // origin — the stylesheet host in style-src, the file host in font-src.
    expect(directives.get("style-src")).toBe("'self' 'unsafe-inline' https://fonts.googleapis.com");
    expect(directives.get("font-src")).toBe("'self' https://fonts.gstatic.com");
    for (const name of ["script-src", "default-src", "connect-src", "img-src"]) {
      expect(directives.get(name)).not.toContain("unsafe-inline");
      expect(directives.get(name)).not.toContain("unsafe-eval");
      expect(directives.get(name)).not.toContain("fonts.googleapis.com");
      expect(directives.get(name)).not.toContain("fonts.gstatic.com");
    }
    expect(HEADERS_CONF).toContain("it is the one relaxation here and it is");
    expect(HEADERS_CONF).toContain("the one deliberate");
  });

  it("is included by the server block and by every location with its own headers", () => {
    // nginx drops inherited add_header lines inside a location that has its own.
    // Day 25 added two such locations (sw.js and the manifest), so: four.
    const includes = NGINX_CONF.match(/include \/etc\/nginx\/security-headers\.conf;/g) ?? [];
    expect(includes).toHaveLength(4);
    for (const location of [
      "location \\/assets\\/",
      "location = \\/sw\\.js",
      "location = \\/manifest\\.webmanifest",
    ]) {
      expect(NGINX_CONF).toMatch(
        new RegExp(`${location} \\{[^}]*include \\/etc\\/nginx\\/security-headers\\.conf;`),
      );
    }
    // Every location that sets a header includes the security headers too.
    const blocks = NGINX_CONF.match(/location[^{]*\{[^}]*\}/g) ?? [];
    for (const block of blocks) {
      if (block.includes("add_header")) {
        expect(block, block.split("{")[0]).toContain("include /etc/nginx/security-headers.conf;");
      }
    }
  });

  it("never lets a stale service worker or manifest be cached (Day 25)", () => {
    expect(NGINX_CONF).toMatch(/location = \/sw\.js \{[^}]*Cache-Control "no-cache"/);
    expect(NGINX_CONF).toMatch(
      /location = \/manifest\.webmanifest \{[^}]*application\/manifest\+json[^}]*Cache-Control "no-cache"/,
    );
  });

  it("sends the same non-CSP headers the API does", () => {
    for (const header of [
      "X-Content-Type-Options nosniff",
      "X-Frame-Options DENY",
      "Referrer-Policy no-referrer",
      "Permissions-Policy",
      "X-Permitted-Cross-Domain-Policies none",
    ]) {
      expect(HEADERS_CONF).toContain(header);
    }
  });
});

describe("the dev server is looser than production, on purpose", () => {
  it("documented the reason it needs unsafe-inline", () => {
    expect(VITE_CONFIG).toContain("@vitejs/plugin-react");
    expect(VITE_CONFIG).toContain("Refresh preamble");
  });

  it("still pins everything else to self", () => {
    expect(VITE_CONFIG).toContain("\"default-src 'self'\"");
    expect(VITE_CONFIG).toContain("\"object-src 'none'\"");
    expect(VITE_CONFIG).toContain("\"frame-ancestors 'none'\"");
    // The relaxation must not leak into the production snippet.
    expect(HEADERS_CONF).not.toContain("unsafe-eval");
  });
});
