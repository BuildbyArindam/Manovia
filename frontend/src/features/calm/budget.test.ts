/**
 * The calm space's asset and network budget (Day 46).
 *
 * Two things are being protected here:
 *
 * 1. **No runtime download.** The scene is drawn from code and a handful of
 *    hex colours. A texture, a font, a model or a fetch would each be one more
 *    thing that can fail, stall or surprise someone in a headset — and the
 *    brief forbids external CDN assets outright.
 * 2. **A stated size.** The calm route's own chunk is budgeted at 25 KB gzip in
 *    `scripts/check-bundle-budget.mjs` (the generic route increment); the last
 *    measured build was ~5.8 KB, because there is no 3D library. This test pins
 *    the *source-level* budget too, so adding an import is a failing test rather
 *    than a surprise at build time.
 *
 * The numbers below are not aspirational: they fail the build when crossed.
 */

import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const HERE = dirname(fileURLToPath(import.meta.url));

/** Files that can ship in the browser (tests and test helpers are excluded). */
function shippedSources(): Array<{ name: string; source: string; bytes: number }> {
  return readdirSync(HERE)
    .filter((name) => /\.(ts|tsx)$/.test(name))
    .filter((name) => !name.endsWith(".test.ts") && !name.endsWith(".test.tsx"))
    .filter((name) => name !== "testGl.ts")
    .map((name) => {
      const path = join(HERE, name);
      return {
        name,
        source: readFileSync(path, "utf8"),
        bytes: statSync(path).size,
      };
    });
}

describe("the calm space's runtime budget", () => {
  it("loads nothing at runtime: no fetch, no images, no external URLs", () => {
    for (const file of shippedSources()) {
      const code = file.source.replace(/\/\*[\s\S]*?\*\//g, " ").replace(/\/\/.*$/gm, " ");
      expect(code, `${file.name} must not fetch`).not.toMatch(/\bfetch\s*\(/);
      expect(code, `${file.name} must not build image objects`).not.toMatch(/new Image\(/);
      expect(code, `${file.name} must not reference a URL`).not.toMatch(/https?:\/\//);
      expect(code, `${file.name} must not load an audio file`).not.toMatch(/decodeAudioData/);
      expect(code, `${file.name} must not pull a CDN script`).not.toMatch(
        /createElement\("script"\)/,
      );
    }
  });

  it("has no 3D library dependency", () => {
    const dependencies = [
      ...shippedSources().map((file) => file.source.match(/from "([^"]+)"/g) ?? []),
    ].flat();
    const imports = dependencies.join(" ");
    expect(imports).not.toMatch(/three|aframe|babylon/i);
  });

  it("keeps its own source small enough to read in one sitting", () => {
    const total = shippedSources().reduce((sum, file) => sum + file.bytes, 0);
    // ~60 KB of TypeScript across the whole feature, comments included.
    expect(total).toBeLessThan(80 * 1024);
    // Pinned in the renderer: 36 triangles for the 3D scene.
    const scene = readFileSync(join(HERE, "renderWebGL.ts"), "utf8");
    expect(scene).toContain("drawArrays(gl.TRIANGLES");
  });

  it("draws the 2D fallback in bounded work per frame", () => {
    const renderer = readFileSync(join(HERE, "render2d.ts"), "utf8");
    // No loops over unbounded data, no per-frame allocation of arrays.
    expect(renderer).not.toMatch(/for\s*\(\s*let[^)]*\.length\s*<\s*\w+\.length/);
    expect(renderer).not.toMatch(/new Array|Array\.from\(\{/);
  });
});
