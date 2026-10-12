/**
 * Pseudo-localisation (Day 23).
 *
 * The pseudo mode is the hard-coded-string and overflow detector, so its
 * guarantees get tested directly: every letter must become unreadable-as-English,
 * the output must grow by the expansion ratio, the brackets must wrap it, and —
 * critically — real locales must pass through untouched even when pseudo mode is
 * registered in the pipeline.
 */

import i18next from "i18next";
import { describe, expect, it } from "vitest";

import en from "./locales/en.json";
import { pseudoLocalise, pseudoPostProcessor } from "./pseudo";

describe("pseudoLocalise", () => {
  it("wraps the result in the marker brackets", () => {
    const out = pseudoLocalise("Save");
    expect(out.startsWith("⟦")).toBe(true);
    expect(out.endsWith("⟧")).toBe(true);
  });

  it("accents every ASCII letter so plain English stands out", () => {
    const out = pseudoLocalise("Save check-in");
    for (const letter of "Savecheckin") {
      expect(out).not.toContain(letter);
    }
  });

  it("leaves digits and punctuation alone", () => {
    const out = pseudoLocalise("Step 2 of 7!");
    expect(out).toContain("2");
    expect(out).toContain("7");
    expect(out).toContain("!");
  });

  it("expands by the ratio with padding", () => {
    const input = "The plan suggests at most three small things a day";
    const out = pseudoLocalise(input);
    expect(out.length).toBeGreaterThanOrEqual(Math.floor(input.length * 1.35));
    expect(out).toContain("~");
  });
});

describe("pseudoPostProcessor", () => {
  // Typed locally: the module interface's `options` is i18next's `TOptions`,
  // but the processor only ever reads `lng`.
  const process = pseudoPostProcessor.process as (
    value: string,
    key: string,
    options: { lng?: string | string[] },
  ) => string;

  it("ignores real languages", () => {
    expect(process("Saved.", "k", { lng: "en" })).toBe("Saved.");
    expect(process("Saved.", "k", { lng: "hi" })).toBe("Saved.");
    expect(process("Saved.", "k", { lng: ["en", "hi"] })).toBe("Saved.");
  });

  it("transforms only the pseudo language", () => {
    const out = process("Saved.", "k", { lng: "pseudo" });
    expect(out.startsWith("⟦")).toBe(true);
    expect(out).not.toContain("Saved.");
  });
});

describe("a full i18next instance in pseudo mode", () => {
  it("renders pseudo copy while the fallback supplies the strings", async () => {
    const instance = i18next.createInstance();
    await instance.use(pseudoPostProcessor).init({
      // Mirrors the app's wiring: pseudo has its own resource entry pointing
      // at the English tree, which is what routes it through the post-processor.
      resources: { en: { translation: en }, pseudo: { translation: en } },
      lng: "pseudo",
      fallbackLng: "en",
      // Registration via use() is not enough; the option activates it (same
      // wiring as src/lib/i18n/i18n.ts — keep them in lockstep).
      postProcess: [pseudoPostProcessor.name],
      interpolation: { escapeValue: false },
      returnNull: false,
    });
    const heading = instance.t("mood.heading") as string;
    expect(heading.startsWith("⟦")).toBe(true);
    // Every ASCII letter of the original is accented away.
    expect(heading).not.toContain("Mood");
  });

  it("keeps interpolation working inside pseudo output", async () => {
    const instance = i18next.createInstance();
    await instance.use(pseudoPostProcessor).init({
      resources: { en: { translation: en }, pseudo: { translation: en } },
      lng: "pseudo",
      fallbackLng: "en",
      postProcess: [pseudoPostProcessor.name],
      interpolation: { escapeValue: false },
      returnNull: false,
    });
    const line = instance.t("mood.noteRemaining", { count: 42 }) as string;
    expect(line).toContain("42");
    expect(line.startsWith("⟦")).toBe(true);
  });
});
