/**
 * The output guard, held to the server's verdicts.
 *
 * AGENTS.md safety rule 4 applies on the device exactly as it does on the server:
 * every model reply passes a deterministic check before it is shown. Private Mode
 * runs a smaller model, so the check matters more here, not less.
 *
 * The comparison is against `evals/parity/python-output-guard.json` — what
 * `RuleBasedOutputGuard` decides for 36 candidate replies, written by
 * `evals/parity/export_output_guard.py`. Compared per probe: whether it passed, the
 * first failing category, every failing category, and the text the guard returns
 * (the original reply, or the pre-written safe reply in the expected language).
 *
 * The shingles for the prompt-echo check are built from the same
 * `content/prompts/system_v*.md` files Python reads, so that check is compared
 * rather than merely reimplemented.
 */

import { describe, expect, it } from "vitest";

import type { RiskLevel } from "@/lib/endpoints";

import {
  OUTPUT_GUARD_PATH,
  readBundle,
  readPromptTexts,
  readText,
} from "../testSupport/repoArtifacts";
import { compilePatternSet } from "./bundleSchema";
import {
  DEFAULT_MAX_WORDS,
  RuleBasedOutputGuard,
  buildOutputGuard,
  echoesPrompt,
  promptShingles,
  scriptOf,
} from "./outputGuard";
import { Normaliser } from "./normalise";

interface PythonProbe {
  id: string;
  note: string;
  text: string;
  risk: string;
  language: string | null;
  passed: boolean;
  reason: string | null;
  categories: string[];
  text_out: string;
}

interface PythonOutputGuard {
  format: string;
  count: number;
  max_words: number;
  prompt_echo: { words: number; min_covered: number };
  prompt_sources: string[];
  cases: PythonProbe[];
}

const bundle = readBundle();
const patterns = compilePatternSet(bundle);
const document = JSON.parse(readText(OUTPUT_GUARD_PATH)) as PythonOutputGuard;
const prompts = readPromptTexts();
const guard = new RuleBasedOutputGuard(bundle, patterns, { promptTexts: prompts });
const normaliser = new Normaliser(patterns);

function verdictFor(probe: PythonProbe) {
  return guard.check(probe.text, {
    risk: probe.risk as RiskLevel,
    expectedLanguage: probe.language,
  });
}

describe("output guard parity with Python", () => {
  it("reads the whole probe list", () => {
    expect(document.format).toBe("manovia-parity-output-guard/1");
    expect(document.cases).toHaveLength(document.count);
    expect(document.max_words).toBe(DEFAULT_MAX_WORDS);
    expect(document.prompt_echo).toEqual({ words: 8, min_covered: 20 });
    expect(prompts).toHaveLength(document.prompt_sources.length);
    // The bundle carries the same prompts, which is what lets the guard run offline:
    // `buildOutputGuard` must produce the same shingles as reading them from disk.
    const shipped = Object.keys(bundle.prompts)
      .sort()
      .map((key) => bundle.prompts[key]?.text ?? "");
    expect(shipped).toEqual(prompts);
    expect(
      buildOutputGuard(bundle, patterns).check(document.cases[0]?.text ?? "", {
        risk: (document.cases[0]?.risk ?? "none") as RiskLevel,
      }).passed,
    ).toBe(document.cases[0]?.passed);
  });

  it.each(document.cases)("decides $id the same way", (probe) => {
    const verdict = verdictFor(probe);
    expect({
      passed: verdict.passed,
      reason: verdict.reason,
      categories: verdict.categories,
      text: verdict.text,
    }).toEqual({
      passed: probe.passed,
      reason: probe.reason,
      categories: probe.categories,
      text: probe.text_out,
    });
  });

  it("refuses what Python refuses, and for the same first reason", () => {
    const refused = document.cases.filter((probe) => !probe.passed);
    expect(refused.length).toBeGreaterThan(20);
    for (const probe of refused) {
      expect(verdictFor(probe).reason, probe.id).toBe(probe.reason);
    }
  });

  it("covers every category in the closed vocabulary", () => {
    const seen = new Set(document.cases.flatMap((probe) => probe.categories));
    for (const category of [
      "diagnosis",
      "medication",
      "self_harm_method",
      "identity_claim",
      "outcome_promise",
      "isolation_dependence",
      "too_long",
      "wrong_language",
      "missing_check_in",
      "prompt_leak",
      "jailbreak",
    ]) {
      expect(seen, `no probe exercises ${category}`).toContain(category);
    }
  });
});

describe("output guard behaviour", () => {
  it("needs the whole reply, because a promise can span sentences", () => {
    expect(guard.requiresFullText).toBe(true);
  });

  it("returns short category codes, so a verdict can never leak reply text", () => {
    // The verdict is what gets logged and rendered. A code that long or that
    // free-form could carry a fragment of the reply with it, which is the thing the
    // no-raw-text rule (AGENTS.md safety rule 5) exists to prevent.
    const known = new Set([
      "diagnosis",
      "medication",
      "self_harm_method",
      "identity_claim",
      "outcome_promise",
      "isolation_dependence",
      "too_long",
      "wrong_language",
      "missing_check_in",
      "prompt_leak",
      "jailbreak",
    ]);
    for (const probe of document.cases) {
      const verdict = verdictFor(probe);
      expect(verdict.reason === null || known.has(verdict.reason), probe.id).toBe(true);
      for (const category of verdict.categories) {
        expect(known, probe.id).toContain(category);
        expect(category.length).toBeLessThanOrEqual(20);
      }
    }
  });

  it("has a pre-written safe reply in every shipped locale", () => {
    for (const locale of ["en", "hi", "bn", "hi-IN", "zz"]) {
      expect(guard.safeReply(locale).trim().length).toBeGreaterThan(20);
    }
    expect(guard.safeReply("hi")).toBe(bundle.output_guard.safe_replies.hi);
    expect(guard.safeReply("zz")).toBe(bundle.output_guard.safe_replies.en);
    expect(guard.trimmedClosing("bn")).toBe(bundle.output_guard.trimmed_closings.bn);
  });

  it("detects script, which is the part of the language check that runs offline", () => {
    expect(scriptOf("आप कैसे हैं?")).toBe("devanagari");
    expect(scriptOf("আপনি কেমন আছেন?")).toBe("bengali");
    expect(scriptOf("how are you today")).toBe("latin");
    expect(scriptOf("1234 !!!")).toBe("none");
    expect(scriptOf("")).toBe("none");
    // Romanised Hindi reads as Latin: accepted, because users code-switch and the
    // prompt allows that register. The statistical detector that would call it
    // Hindi has no offline browser equivalent — see the module comment.
    expect(scriptOf("aap kaise hain")).toBe("latin");
  });

  it("measures prompt echoes as coverage, not one lucky run", () => {
    const shingles = promptShingles(prompts, normaliser);
    expect(shingles.size).toBeGreaterThan(100);
    const echo = document.cases.find((probe) => probe.id === "prompt_echo");
    expect(echo).toBeDefined();
    const normalized = normaliser.toPrimary(normaliser.strip(echo?.text ?? ""));
    expect(echoesPrompt(normalized, shingles, patterns.tables)).toBe(true);
    // Good advice that also appears in the prompt is not an echo: one run is not
    // enough, the reply has to reuse at least 20 words that way.
    const advice =
      "Tell one trusted person, a friend, a family member, or a helpline, and stay with them.";
    expect(echoesPrompt(normaliser.toPrimary(advice), shingles, patterns.tables)).toBe(false);
  });

  it("refuses a reply longer than the limit and keeps a short one", () => {
    const long = `${"word ".repeat(DEFAULT_MAX_WORDS + 5)}how are you feeling?`;
    expect(guard.check(long, { risk: "low" }).categories).toContain("too_long");
    const short = "That sounds heavy. How are you feeling right now?";
    expect(guard.check(short, { risk: "low" }).passed).toBe(true);
  });

  it("insists on a check-in when the turn was MEDIUM or worse", () => {
    expect(guard.check("Breathing slowly can help.", { risk: "medium" }).categories).toContain(
      "missing_check_in",
    );
    expect(
      guard.check("Breathing slowly can help. How are you feeling?", { risk: "medium" }).passed,
    ).toBe(true);
    // At LOW and NONE the same reply is fine: nobody is forced to ask a question.
    expect(guard.check("Breathing slowly can help.", { risk: "low" }).passed).toBe(true);
  });
});
