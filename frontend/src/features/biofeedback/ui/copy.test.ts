/**
 * The biofeedback copy contract (Day 42).
 *
 * Two guards, both required by the day's scope:
 *
 *   1. **No forbidden claims** in any `biofeedback.*` string in any language:
 *      nothing about healthy/unhealthy HRV, nothing diagnosing, treating or
 *      curing, no medical-claim phrasing, no good/bad framing of HRV values.
 *   2. **Every key the code uses exists** in all three locales (and the three
 *      locales agree), so the UI can never render a raw key or drift.
 */

import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const LOCALES_DIR = join(__dirname, "../../../lib/i18n/locales");
const FEATURE_DIR = join(__dirname, "..");

const languages = readdirSync(LOCALES_DIR)
  .filter((name) => name.endsWith(".json"))
  .map((name) => name.replace(".json", ""));

interface Strings {
  [key: string]: string | Strings;
}

function flatten(value: Strings, prefix: string, out: Map<string, string>): void {
  for (const [key, entry] of Object.entries(value)) {
    const path = `${prefix}${key}`;
    if (typeof entry === "string") {
      out.set(path, entry);
    } else {
      flatten(entry, `${path}.`, out);
    }
  }
}

function loadBiofeedbackStrings(lang: string): Map<string, string> {
  const raw = JSON.parse(readFileSync(join(LOCALES_DIR, `${lang}.json`), "utf8")) as Strings;
  const out = new Map<string, string>();
  flatten((raw["biofeedback"] as Strings) ?? {}, "biofeedback.", out);
  return out;
}

/**
 * Claims this feature must never make. A keyword match is a violation unless
 * the wording explicitly negates it in the preceding words ("does not
 * diagnose", "not a medical device") — the required disclaimer must keep
 * working. Negation is recognised in en/hi/bn.
 */
const forbidden: Array<{ keyword: RegExp; why: string }> = [
  { keyword: /healthy\s*h?rv|unhealthy\s*h?rv/gi, why: "labels HRV healthy/unhealthy" },
  { keyword: /good\s*h?rv|bad\s*h?rv|better\s*h?rv|worse\s*h?rv/gi, why: "judges HRV good/bad" },
  { keyword: /diagnos/gi, why: "diagnosis language" },
  { keyword: /\btreat|cure/gi, why: "treatment/cure language" },
  { keyword: /medical device|medical instrument|चिकित्सा यंत्र|চিকিৎসাযন্ত্র/gi, why: "medical device status" },
  { keyword: /clinically (proven|validated|significant)/gi, why: "clinical claim" },
  { keyword: /disease|disorder|arrhythmia/gi, why: "medical condition claim" },
  { keyword: /risk (score|of)|predict/gi, why: "risk/prediction claim" },
  { keyword: /guarantee|will (improve|increase|lower|reduce)/gi, why: "outcome promise" },
];

const NEGATION = /not |never |no |नहीं|নয়|না /i;

function findViolations(key: string, value: string): string[] {
  const violations: string[] = [];
  for (const { keyword, why } of forbidden) {
    const re = new RegExp(keyword.source, keyword.flags);
    for (const match of value.matchAll(re)) {
      const index = match.index ?? 0;
      // Negation can sit before (en "does not diagnose", hi "नहीं") or after
      // the term (bn "চিকিৎসাযন্ত্র নয়", hi "यंत्र नहीं") — check both sides.
      const before = value.slice(Math.max(0, index - 60), index);
      const after = value.slice(index + match[0].length, index + match[0].length + 24);
      if (NEGATION.test(before) || NEGATION.test(after)) {
        continue; // disclaimers are required, not forbidden
      }
      violations.push(`${key} [${why}]: …${value.slice(Math.max(0, index - 20), index + 40)}…`);
    }
  }
  return violations;
}

describe("biofeedback copy contract", () => {
  it.each(languages)("%s: no forbidden claims in biofeedback strings", (lang) => {
    const strings = loadBiofeedbackStrings(lang);
    expect(strings.size).toBeGreaterThan(40);
    const violations: string[] = [];
    for (const [key, value] of strings) {
      violations.push(...findViolations(key, value));
    }
    expect(violations).toEqual([]);
  });

  it("the three locales carry the same biofeedback keys and placeholders", () => {
    const reference = loadBiofeedbackStrings("en");
    for (const lang of languages) {
      const strings = loadBiofeedbackStrings(lang);
      expect([...strings.keys()].sort()).toEqual([...reference.keys()].sort());
      for (const [key, value] of reference) {
        const placeholders = [...value.matchAll(/\{\{(\w+)\}\}/g)].map((m) => m[1]).sort();
        const other = [...(strings.get(key) ?? "").matchAll(/\{\{(\w+)\}\}/g)]
          .map((m) => m[1])
          .sort();
        expect(other, `${lang}:${key} placeholders`).toEqual(placeholders);
      }
    }
  });

  it("every biofeedback key used in the code exists in en", () => {
    const en = loadBiofeedbackStrings("en");
    const used = new Set<string>();
    const walk = (dir: string): void => {
      for (const entry of readdirSync(dir, { withFileTypes: true })) {
        const full = join(dir, entry.name);
        if (entry.isDirectory()) {
          walk(full);
        } else if (/\.(ts|tsx)$/.test(entry.name) && !entry.name.endsWith(".test.ts") && !entry.name.endsWith(".test.tsx")) {
          const source = readFileSync(full, "utf8");
          for (const match of source.matchAll(/["'`](biofeedback\.[a-zA-Z0-9_.]+)["'`]/g)) {
            used.add(match[1] ?? "");
          }
          // moodN is built dynamically in SummaryPanel.
          if (/biofeedback\.session\.mood\$\{/.test(source)) {
            for (const n of [1, 2, 3, 4, 5]) {
              used.add(`biofeedback.session.mood${n}`);
            }
          }
        }
      }
    };
    walk(FEATURE_DIR);
    expect(used.size).toBeGreaterThan(40);
    const missing = [...used].filter((key) => !en.has(key)).sort();
    expect(missing).toEqual([]);
  });
});
