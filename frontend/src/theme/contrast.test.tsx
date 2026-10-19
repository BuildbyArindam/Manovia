/**
 * Day 25: high-contrast mode — the palettes (asserted from index.css), the
 * Settings switch, and the first-paint resolution that follows the operating
 * system's `prefers-contrast: more` when no choice was made.
 */

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { act, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { contrastRatio } from "@/lib/contrast";

import { CONTRAST_STORAGE_KEY } from "./theme-storage";
import { ThemeProvider, useTheme } from "./ThemeProvider";

const HERE = dirname(fileURLToPath(import.meta.url));
const CSS = readFileSync(join(HERE, "..", "index.css"), "utf8").replace(/\/\*[\s\S]*?\*\//g, " ");

function palette(selector: string): Record<string, string> {
  // Day 46: the high-contrast block now answers two selectors (the Appearance
  // switch and the sensory profile), so find the selector inside a list rather
  // than assuming `selector {` appears verbatim.
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = new RegExp(`(?:^|,)\\s*${escaped}\\s*(?:,|\\{)`, "m").exec(CSS);
  expect(match, `no block for ${selector}`).not.toBeNull();
  const start = CSS.indexOf("{", match?.index ?? 0);
  const body = CSS.slice(start + 1, CSS.indexOf("}", start));
  return Object.fromEntries(
    [...body.matchAll(/(--manovia-[a-z-]+):\s*(#[0-9a-f]{6})/gi)].map((match) => [
      match[1],
      match[2],
    ]),
  );
}

const HC_LIGHT = palette(':root[data-contrast="more"]');
const HC_DARK = palette(':root[data-theme="dark"][data-contrast="more"]');

const TEXT_PAIRS: ReadonlyArray<readonly [string, string[]]> = [
  ["--manovia-text", ["--manovia-bg", "--manovia-surface", "--manovia-surface-muted"]],
  ["--manovia-text-muted", ["--manovia-bg", "--manovia-surface", "--manovia-surface-muted"]],
  ["--manovia-accent-fg", ["--manovia-accent-bg"]],
  ["--manovia-accent-text", ["--manovia-bg", "--manovia-surface", "--manovia-accent-soft"]],
  ["--manovia-danger-fg", ["--manovia-danger-bg"]],
  ["--manovia-danger-text", ["--manovia-bg", "--manovia-surface", "--manovia-danger-soft"]],
];

describe("high-contrast palettes", () => {
  it.each([
    ["light", HC_LIGHT],
    ["dark", HC_DARK],
  ])("keeps every %s text pair at or above 7:1 (WCAG AAA)", (_name, tokens) => {
    const failures: string[] = [];
    for (const [foreground, backgrounds] of TEXT_PAIRS) {
      for (const background of backgrounds) {
        const ratio = contrastRatio(tokens[foreground] ?? "", tokens[background] ?? "");
        if (ratio < 7) {
          failures.push(`${foreground} on ${background}: ${ratio.toFixed(2)}`);
        }
      }
    }
    expect(failures).toEqual([]);
  });

  it.each([
    ["light", HC_LIGHT],
    ["dark", HC_DARK],
  ])("keeps the %s focus ring and both borders at or above 4.5:1", (_name, tokens) => {
    for (const token of ["--manovia-focus", "--manovia-border-strong", "--manovia-border"]) {
      for (const background of ["--manovia-bg", "--manovia-surface", "--manovia-surface-muted"]) {
        expect(
          contrastRatio(tokens[token] ?? "", tokens[background] ?? ""),
          `${token} on ${background}`,
        ).toBeGreaterThanOrEqual(4.5);
      }
    }
  });

  it("gives the sensory profile's high-contrast choice the same palette", () => {
    // One palette, two ways to ask for it: the Appearance switch and the
    // sensory profile must never drift apart.
    expect(palette(':root[data-sensory-colours="high-contrast"]')).toEqual(HC_LIGHT);
    expect(palette(':root[data-theme="dark"][data-sensory-colours="high-contrast"]')).toEqual(
      HC_DARK,
    );
  });

  it("defines the same colour tokens as the standard palette", () => {
    const standard = palette(":root");
    expect(Object.keys(HC_LIGHT).sort()).toEqual(Object.keys(standard).sort());
    expect(Object.keys(HC_DARK).sort()).toEqual(Object.keys(standard).sort());
  });

  it("supports forced colours (Windows High Contrast) for state shown by colour alone", () => {
    expect(CSS).toContain("@media (forced-colors: active)");
    expect(CSS).toMatch(/\[aria-pressed="true"\][\s\S]*Highlight/);
  });
});

function ContrastProbe() {
  const { contrast, resolvedContrast, setContrast } = useTheme();
  return (
    <div>
      <p data-testid="probe">{`${contrast}/${resolvedContrast}`}</p>
      <button type="button" onClick={() => setContrast("more")}>
        more
      </button>
      <button type="button" onClick={() => setContrast("system")}>
        system
      </button>
    </div>
  );
}

function stubSystemContrast(more: boolean): void {
  vi.stubGlobal(
    "matchMedia",
    vi.fn((query: string) => ({
      matches: query.includes("prefers-contrast") ? more : false,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  );
}

describe("the contrast switch", () => {
  it("applies and remembers an explicit choice", () => {
    stubSystemContrast(false);
    render(
      <ThemeProvider>
        <ContrastProbe />
      </ThemeProvider>,
    );
    expect(screen.getByTestId("probe")).toHaveTextContent("system/standard");
    expect(document.documentElement.hasAttribute("data-contrast")).toBe(false);

    act(() => {
      screen.getByRole("button", { name: "more" }).click();
    });
    expect(document.documentElement.getAttribute("data-contrast")).toBe("more");
    expect(window.localStorage.getItem(CONTRAST_STORAGE_KEY)).toBe("more");

    act(() => {
      screen.getByRole("button", { name: "system" }).click();
    });
    expect(window.localStorage.getItem(CONTRAST_STORAGE_KEY)).toBeNull();
    expect(document.documentElement.hasAttribute("data-contrast")).toBe(false);
  });

  it("follows the operating system when no choice was made", () => {
    stubSystemContrast(true);
    render(
      <ThemeProvider>
        <ContrastProbe />
      </ThemeProvider>,
    );
    expect(screen.getByTestId("probe")).toHaveTextContent("system/more");
    expect(document.documentElement.getAttribute("data-contrast")).toBe("more");
  });

  it("is resolved before the first paint, by the same rule", async () => {
    stubSystemContrast(true);
    document.documentElement.removeAttribute("data-contrast");
    const { applyFirstPaintContrast } = await import("./first-paint");
    expect(applyFirstPaintContrast()).toBe("more");
    window.localStorage.setItem(CONTRAST_STORAGE_KEY, "standard");
    expect(applyFirstPaintContrast()).toBe("standard");
    expect(document.documentElement.hasAttribute("data-contrast")).toBe(false);
    window.localStorage.setItem(CONTRAST_STORAGE_KEY, "nonsense");
    expect(applyFirstPaintContrast()).toBe("more");
  });
});
