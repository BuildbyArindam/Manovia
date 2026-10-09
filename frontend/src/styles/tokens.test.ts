/**
 * Design-token guardrails.
 *
 * The palette lives in CSS variables, so nothing else in the codebase can check
 * it — this test parses `src/index.css` and asserts the WCAG numbers for both
 * themes. Changing a colour without checking contrast fails here, not in a
 * manual audit. It also pins the two accessibility rules that are easy to lose
 * in a refactor: a 16px base font size and a real `prefers-reduced-motion`
 * block.
 */

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { contrastRatio } from "@/lib/contrast";

// Built from `import.meta.url` rather than `new URL("../index.css", ...)`,
// which Vite would treat as an asset reference and rewrite.
const HERE = dirname(fileURLToPath(import.meta.url));
const CSS = readFileSync(join(HERE, "..", "index.css"), "utf8");

interface CssBlock {
  selectors: string[];
  parents: string[];
  declarations: Map<string, string>;
}

/** Enough of a CSS parser to reach the declarations inside nested blocks. */
function parseBlocks(raw: string): CssBlock[] {
  // Comments would otherwise end up glued to the selector that follows them.
  const css = raw.replace(/\/\*[\s\S]*?\*\//g, " ");
  const blocks: CssBlock[] = [];
  const stack: CssBlock[] = [];
  let buffer = "";

  const saveDeclaration = (): void => {
    const declaration = buffer.trim();
    buffer = "";
    if (declaration === "") {
      return;
    }
    const separator = declaration.indexOf(":");
    if (separator === -1) {
      return;
    }
    const name = declaration.slice(0, separator).trim();
    const value = declaration.slice(separator + 1).trim();
    const target = stack[stack.length - 1];
    if (target !== undefined && name !== "") {
      target.declarations.set(name, value);
    }
  };

  for (const char of css) {
    if (char === "{") {
      const selector = buffer.trim();
      buffer = "";
      stack.push({
        selectors: selector.split(",").map((part) => part.trim()),
        parents: [],
        declarations: new Map(),
      });
      for (const open of stack.slice(0, -1)) {
        stack[stack.length - 1]?.parents.push(...open.selectors);
      }
    } else if (char === "}") {
      saveDeclaration();
      const finished = stack.pop();
      if (finished !== undefined) {
        blocks.push(finished);
      }
    } else if (char === ";") {
      saveDeclaration();
    } else {
      buffer += char;
    }
  }

  return blocks;
}

const BLOCKS = parseBlocks(CSS);

function blockWithSelector(selector: string): CssBlock | undefined {
  return BLOCKS.find((block) => block.selectors.includes(selector));
}

function tokensOf(selector: string): Record<string, string> {
  const block = blockWithSelector(selector);
  if (block === undefined) {
    throw new Error(`No CSS block found for ${selector}`);
  }
  const tokens: Record<string, string> = {};
  for (const [name, value] of block.declarations) {
    if (name.startsWith("--manovia-")) {
      tokens[name] = value;
    }
  }
  return tokens;
}

const LIGHT = tokensOf(":root");
const DARK = tokensOf('[data-theme="dark"]');

/** Font stacks are theme-independent, so they live in `:root` only. */
const COLOR_TOKENS = /^--manovia-(?!font-)/;

function colorTokens(tokens: Record<string, string>): string[] {
  return Object.keys(tokens)
    .filter((name) => COLOR_TOKENS.test(name))
    .sort();
}

/** Every text-on-background pair the app can produce. */
const TEXT_PAIRS: ReadonlyArray<readonly [string, string[]]> = [
  ["--manovia-text", ["--manovia-bg", "--manovia-surface", "--manovia-surface-muted"]],
  ["--manovia-text-muted", ["--manovia-bg", "--manovia-surface", "--manovia-surface-muted"]],
  ["--manovia-accent-fg", ["--manovia-accent-bg"]],
  ["--manovia-accent-text", ["--manovia-bg", "--manovia-surface", "--manovia-accent-soft"]],
  ["--manovia-danger-fg", ["--manovia-danger-bg"]],
  ["--manovia-danger-text", ["--manovia-bg", "--manovia-surface", "--manovia-danger-soft"]],
];

const MINIMUM_TEXT_CONTRAST = 4.5;
const MINIMUM_NON_TEXT_CONTRAST = 3;

describe("design tokens", () => {
  it("defines both palettes with the same colour tokens", () => {
    expect(colorTokens(LIGHT).length).toBeGreaterThan(10);
    expect(colorTokens(DARK)).toEqual(colorTokens(LIGHT));
  });

  it("declares the font stacks once, in :root", () => {
    expect(LIGHT["--manovia-font-sans"]).toContain("system-ui");
    expect(LIGHT["--manovia-font-serif"]).toContain("serif");
    expect(DARK["--manovia-font-sans"]).toBeUndefined();
  });

  it.each([
    ["light", LIGHT],
    ["dark", DARK],
  ])("keeps every %s text pair at or above 4.5:1", (_theme, tokens) => {
    const failures: string[] = [];
    for (const [foreground, backgrounds] of TEXT_PAIRS) {
      for (const background of backgrounds) {
        const ratio = contrastRatio(tokens[foreground] ?? "", tokens[background] ?? "");
        if (ratio < MINIMUM_TEXT_CONTRAST) {
          failures.push(`${foreground} on ${background}: ${ratio.toFixed(2)}:1`);
        }
      }
    }
    expect(failures).toEqual([]);
  });

  it.each([
    ["light", LIGHT],
    ["dark", DARK],
  ])("keeps the %s focus ring and strong borders at or above 3:1", (_theme, tokens) => {
    for (const background of ["--manovia-bg", "--manovia-surface"]) {
      expect(
        contrastRatio(tokens["--manovia-focus"] ?? "", tokens[background] ?? ""),
      ).toBeGreaterThanOrEqual(MINIMUM_NON_TEXT_CONTRAST);
      expect(
        contrastRatio(tokens["--manovia-border-strong"] ?? "", tokens[background] ?? ""),
      ).toBeGreaterThanOrEqual(MINIMUM_NON_TEXT_CONTRAST);
    }
  });

  it("sets a 16px base font size and never smaller", () => {
    expect(blockWithSelector("html")?.declarations.get("font-size")).toBe("16px");
  });

  it("collapses animation when the user asks for reduced motion", () => {
    const reduced = BLOCKS.filter((block) =>
      block.parents.some((parent) => parent.includes("prefers-reduced-motion: reduce")),
    );
    expect(reduced.length).toBeGreaterThan(0);
    const durations = reduced.flatMap((block) => [
      block.declarations.get("animation-duration"),
      block.declarations.get("transition-duration"),
    ]);
    expect(durations.every((duration) => duration?.includes("0.01ms") ?? false)).toBe(true);
    expect(durations.some((duration) => duration?.includes("!important") ?? false)).toBe(true);
  });

  it("keeps the accent green-dominant (sage), not clinical blue", () => {
    // A guardrail on the design direction, not just the numbers: a blue-dominant
    // accent would drift straight back into the clinical look this palette is
    // meant to avoid.
    for (const tokens of [LIGHT, DARK]) {
      const accent = tokens["--manovia-accent-bg"] ?? "";
      const channels = [accent.slice(1, 3), accent.slice(3, 5), accent.slice(5, 7)].map((part) =>
        Number.parseInt(part, 16),
      );
      const [red = 0, green = 0, blue = 0] = channels;
      expect(green).toBeGreaterThanOrEqual(red);
      expect(green).toBeGreaterThanOrEqual(blue);
    }
  });
});
