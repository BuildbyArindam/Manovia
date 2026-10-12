/**
 * Day 25 low-bandwidth rules that are easy to undo by accident:
 *
 * * Hindi and Bengali are their own chunks, loaded on demand — and switching
 *   to a language that is already in memory stays synchronous.
 * * The Indic web fonts are requested only for a Hindi or Bengali interface,
 *   and no longer from index.html (a render-blocking third-party stylesheet on
 *   every English first paint).
 * * Queries make one real attempt offline (the service worker may answer);
 *   mutations fail fast instead of pausing invisibly.
 */

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it, vi } from "vitest";

import { createQueryClient } from "@/lib/queryClient";
import { currentAppLanguage, isLanguageLoaded, setAppLanguage, t } from "@/lib/i18n/i18n";

const HERE = dirname(fileURLToPath(import.meta.url));
const read = (relative: string): string => readFileSync(join(HERE, relative), "utf8");

describe("locale chunks", () => {
  it("bundles only English statically; Hindi and Bengali are dynamic imports", () => {
    const source = read("i18n/i18n.ts");
    expect(source).toMatch(/import en from "\.\/locales\/en\.json"/);
    expect(source).not.toMatch(/import (hi|bn) from/);
    expect(source).toContain('import("./locales/hi.json")');
    expect(source).toContain('import("./locales/bn.json")');
  });

  it("switches synchronously when the language is already in memory", () => {
    expect(isLanguageLoaded("en")).toBe(true);
    expect(isLanguageLoaded("hi")).toBe(true); // registered by the test setup
    setAppLanguage("hi");
    expect(currentAppLanguage()).toBe("hi");
    expect(document.documentElement.lang).toBe("hi");
    expect(t("pwa.backOnline")).toBe("आप फिर से ऑनलाइन हैं।");
    setAppLanguage("en");
    expect(t("pwa.backOnline")).toBe("You're back online.");
  });
});

describe("Indic web fonts", () => {
  it("are not linked from index.html any more", () => {
    const html = read("../../index.html");
    expect(html).not.toMatch(/<link[^>]+fonts\.googleapis\.com/);
    expect(html).toContain('rel="manifest"');
  });

  it("are added once, only for hi/bn, one family per language", async () => {
    // A fresh module and a clean <head>: earlier tests in this file switched
    // to Hindi, which (correctly) already added the Devanagari sheet.
    document.head
      .querySelectorAll("link[data-script-font], link[rel=preconnect]")
      .forEach((link) => {
        link.remove();
      });
    vi.resetModules();
    const { ensureScriptFonts } = await import("@/lib/i18n/scriptFonts");
    const sheets = (): string[] =>
      Array.from(document.head.querySelectorAll<HTMLLinkElement>("link[data-script-font]")).map(
        (link) => link.href,
      );
    ensureScriptFonts("en");
    expect(sheets()).toEqual([]);
    ensureScriptFonts("bn");
    ensureScriptFonts("bn");
    expect(sheets()).toHaveLength(1);
    expect(sheets()[0]).toContain("Noto+Sans+Bengali");
    expect(sheets()[0]).not.toContain("Devanagari");
    ensureScriptFonts("hi");
    expect(sheets()).toHaveLength(2);
    expect(document.head.querySelectorAll('link[rel="preconnect"]').length).toBe(2);
  });
});

describe("network modes", () => {
  it("lets queries reach the service worker offline and fails writes fast", () => {
    const options = createQueryClient().getDefaultOptions();
    expect(options.queries?.networkMode).toBe("offlineFirst");
    expect(options.mutations?.networkMode).toBe("always");
  });
});
