/**
 * Cross-language parity: 500 messages, one engine per language, identical answers.
 *
 * Private Mode runs a TypeScript port of `backend/app/services/safety` on the
 * device. Two implementations of a safety engine is exactly the situation where a
 * silent difference becomes a wrong answer for a real person, so the difference is
 * not left to review: `evals/parity/corpus.jsonl` holds 500 messages, Python's
 * answers are committed beside them in `evals/parity/python-expected.json`
 * (`evals/parity/export_python_expected.py`), and this file asserts that the port
 * reproduces them.
 *
 * Compared per message, not just the verdict:
 *
 * - `level` — the requirement's headline number;
 * - `matched_categories` — which of the eight risks fired;
 * - `rationale_codes` — which rule ids fired, plus the context codes, in order;
 * - `context` — first person, third person, quoted, fiction frame, timeframe,
 *   negated and figurative hit counts, the language hint, truncation and how many
 *   projections were searched.
 *
 * Two engines that agree on a level while disagreeing on *why* are two engines
 * that will disagree on some message nobody tested, so all four are asserted
 * separately and each failure names the field, the message and both answers.
 *
 * The corpus is deliberately weighted towards the places where Python and
 * JavaScript differ by construction: whitespace membership (U+001C is Python's
 * `\s` and not JavaScript's; U+FEFF is the reverse), code points versus UTF-16
 * units, case folding versus `toLowerCase()`, homoglyphs and combining marks,
 * every obfuscation projection, and messages long enough to be windowed.
 */

import { beforeAll, describe, expect, it } from "vitest";

import {
  BUNDLE_PATH,
  CORPUS_PATH,
  expand,
  readBundle,
  readCorpus,
  readExpectations,
  sha256File,
  type CorpusEntry,
  type PythonExpectation,
} from "../testSupport/repoArtifacts";
import { buildRuleEngine } from "./ruleEngine";
import { compilePatternSet } from "./bundleSchema";
import type { RiskAssessment } from "./ruleEngine";

interface Comparison {
  entry: CorpusEntry;
  expected: PythonExpectation;
  actual: RiskAssessment;
  text: string;
}

interface Mismatch {
  id: string;
  group: string;
  field: string;
  expected: unknown;
  actual: unknown;
  text: string;
}

const CORPUS_SIZE = 500;
const PREVIEW_LENGTH = 72;

let comparisons: Comparison[] = [];

function preview(text: string): string {
  const oneLine = text.replace(/\s+/g, " ");
  const head = [...oneLine].slice(0, PREVIEW_LENGTH).join("");
  return oneLine.length > PREVIEW_LENGTH ? `${head}…` : head;
}

function compareContext(expected: Record<string, unknown>, actual: RiskAssessment): Mismatch[] {
  const found: Mismatch[] = [];
  const context = actual.context as unknown as Record<string, unknown>;
  for (const field of Object.keys(expected).sort()) {
    const left = expected[field];
    const right = context[field];
    if (JSON.stringify(left) !== JSON.stringify(right)) {
      found.push({
        id: "",
        group: "",
        field: `context.${field}`,
        expected: left,
        actual: right,
        text: "",
      });
    }
  }
  return found;
}

function collectMismatches(): Mismatch[] {
  const found: Mismatch[] = [];
  for (const comparison of comparisons) {
    const { entry, expected, actual, text } = comparison;
    const push = (field: string, left: unknown, right: unknown): void => {
      if (JSON.stringify(left) !== JSON.stringify(right)) {
        found.push({
          id: entry.id,
          group: entry.group,
          field,
          expected: left,
          actual: right,
          text: preview(text),
        });
      }
    };
    push("level", expected.level, actual.level);
    push("categories", expected.categories, actual.matched_categories);
    push("codes", expected.codes, actual.rationale_codes);
    for (const mismatch of compareContext(expected.context, actual)) {
      found.push({ ...mismatch, id: entry.id, group: entry.group, text: preview(text) });
    }
  }
  return found;
}

function mismatchesFor(field: string): Mismatch[] {
  return collectMismatches().filter((mismatch) => mismatch.field === field);
}

/** A failure message a person can act on: which message, which field, both answers. */
function report(mismatches: Mismatch[], limit = 25): string {
  const lines = mismatches
    .slice(0, limit)
    .map(
      (mismatch) =>
        `  ${mismatch.id} [${mismatch.group}] ${mismatch.field}\n` +
        `    text:     ${JSON.stringify(mismatch.text)}\n` +
        `    python:   ${JSON.stringify(mismatch.expected)}\n` +
        `    browser:  ${JSON.stringify(mismatch.actual)}`,
    );
  const omitted = mismatches.length - lines.length;
  if (omitted > 0) {
    lines.push(`  … and ${omitted} more`);
  }
  return `${mismatches.length} mismatch(es):\n${lines.join("\n")}`;
}

beforeAll(() => {
  const patterns = compilePatternSet(readBundle());
  const engine = buildRuleEngine(patterns);
  const corpus = readCorpus();
  const expectations = new Map(readExpectations().cases.map((entry) => [entry.id, entry]));

  comparisons = corpus.map((entry) => {
    const expected = expectations.get(entry.id);
    if (expected === undefined) {
      throw new Error(
        `${entry.id} has no Python expectation — regenerate evals/parity/python-expected.json`,
      );
    }
    const text = expand(entry);
    return { entry, expected, text, actual: engine.assess(text, entry.lang ?? null) };
  });
});

describe("Day 41 cross-language parity", () => {
  it("runs the whole corpus through both engines", () => {
    expect(comparisons).toHaveLength(CORPUS_SIZE);
    const ids = new Set(comparisons.map((comparison) => comparison.entry.id));
    expect(ids.size).toBe(CORPUS_SIZE);
  });

  it("gives all 500 messages the identical risk level", () => {
    const mismatches = mismatchesFor("level");
    expect(mismatches, report(mismatches)).toEqual([]);
  });

  it("agrees on which risk categories fired", () => {
    const mismatches = mismatchesFor("categories");
    expect(mismatches, report(mismatches)).toEqual([]);
  });

  it("agrees on the rationale codes, in order", () => {
    const mismatches = mismatchesFor("codes");
    expect(mismatches, report(mismatches)).toEqual([]);
  });

  it("agrees on every context flag and counter", () => {
    const mismatches = collectMismatches().filter((mismatch) =>
      mismatch.field.startsWith("context."),
    );
    expect(mismatches, report(mismatches)).toEqual([]);
  });

  it("covers every risk level and the divergence groups the port is judged on", () => {
    const levels = new Set(comparisons.map((comparison) => comparison.actual.level));
    expect([...levels].sort()).toEqual(["high", "imminent", "low", "medium", "none"]);

    const groups = new Set(comparisons.map((comparison) => comparison.entry.group));
    for (const group of [
      "empty",
      "negation",
      "figurative",
      "third_person",
      "fiction",
      "quotation",
      "self_quote",
      "clauses",
      "indic",
      "romanised",
      "leet",
      "fused",
      "spaced",
      "stretched",
      "misspelling",
      "homoglyph",
      "combining",
      "invisible",
      "whitespace",
      "case",
      "astral",
      "windowed",
    ]) {
      expect(groups, `the corpus lost its ${group} messages`).toContain(group);
    }
  });

  it("is generated from the corpus and the bundle it is checked against", () => {
    const expectations = readExpectations();
    expect(expectations.count).toBe(CORPUS_SIZE);
    expect(expectations.corpus_sha256, "expectations are stale relative to corpus.jsonl").toBe(
      sha256File(CORPUS_PATH),
    );
    expect(
      expectations.bundle_sha256,
      "expectations were generated beside a different bundle",
    ).toBe(sha256File(BUNDLE_PATH));
    expect(expectations.format).toBe("manovia-parity-expectations/1");
  });
});
