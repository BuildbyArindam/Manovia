/**
 * Normalisation parity: the layer under the verdicts.
 *
 * `parity.test.ts` compares what the two engines *decide* for 500 messages. This
 * compares how they *read* the same messages: every projection, every clause
 * offset, every Unicode fold, against
 * `evals/parity/python-normalisation.json` (126 probes written by
 * `evals/parity/export_normalisation.py`).
 *
 * Why both: two ports can agree on 500 verdicts while computing different
 * intermediates, and then disagree on the 501st message. The probes here are the
 * awkward ones — NFKC surprises, case folding versus `toLowerCase()`, the two
 * whitespace classes that disagree about U+001C and U+FEFF, homoglyphs, stacked
 * diacritics, and astral characters, where Python counts code points and
 * JavaScript counts UTF-16 units.
 *
 * Offsets are compared in code points, the unit Python uses. Inside the engine
 * everything stays in UTF-16 units and is self-consistent; the translation here
 * exists so the comparison is honest for a message that contains one.
 */

import { describe, expect, it } from "vitest";

import { NORMALISATION_PATH, readBundle, readText } from "../testSupport/repoArtifacts";
import { compilePatternSet } from "./bundleSchema";
import { Normaliser } from "./normalise";
import { codePointLength } from "./pyStrings";

interface PythonClause {
  text: string;
  start: number;
  end: number;
}

interface PythonProbe {
  id: string;
  text: string;
  stripped: string;
  primary: string;
  leet: string;
  corrected: string;
  clause_text: string;
  clauses: PythonClause[];
  squashed: string;
  collapsed: string;
  confusables: string;
  combining: string;
  first_person: boolean;
  quotes: [string, string];
  variants: string[];
  lengths: { primary: number; clause_text: number; codepoints: number; utf16_units: number };
}

interface PythonNormalisation {
  format: string;
  count: number;
  runtime: { python: string; unicodedata: string };
  cases: PythonProbe[];
}

const patterns = compilePatternSet(readBundle());
const normaliser = new Normaliser(patterns);
const document = JSON.parse(readText(NORMALISATION_PATH)) as PythonNormalisation;

/** A UTF-16 offset in `text`, expressed in code points (Python's unit). */
function toCodePointOffset(text: string, unitOffset: number): number {
  let codePoints = 0;
  let units = 0;
  for (const char of text) {
    if (units >= unitOffset) {
      break;
    }
    units += char.length;
    codePoints += 1;
  }
  return codePoints;
}

describe("normalisation parity with Python", () => {
  it("reads the whole probe list", () => {
    expect(document.format).toBe("manovia-parity-normalisation/1");
    expect(document.cases).toHaveLength(document.count);
    expect(document.count).toBeGreaterThan(100);
  });

  it.each(document.cases)("strips invisible characters identically: $id", (probe) => {
    expect(normaliser.strip(probe.text)).toBe(probe.stripped);
  });

  it.each(document.cases)("produces the same primary projection: $id", (probe) => {
    expect(normaliser.toPrimary(probe.stripped)).toBe(probe.primary);
  });

  it.each(document.cases)("produces the same variant list, in order: $id", (probe) => {
    expect(normaliser.normalise(probe.text).variants).toEqual(probe.variants);
  });

  it.each(document.cases)("applies the leet map identically: $id", (probe) => {
    expect(normaliser.toPrimary(normaliser.applyLeet(probe.stripped))).toBe(probe.leet);
  });

  it.each(document.cases)("corrects the same misspellings: $id", (probe) => {
    expect(normaliser.correctSpellings(probe.primary)).toBe(probe.corrected);
  });

  it.each(document.cases)("squashes and collapses identically: $id", (probe) => {
    const message = normaliser.normalise(probe.text);
    expect(message.squashed).toBe(probe.squashed);
    expect(message.collapsed).toBe(probe.collapsed);
  });

  it.each(document.cases)("marks the same clause boundaries: $id", (probe) => {
    expect(normaliser.toClauseText(probe.stripped)).toBe(probe.clause_text);
    const index = normaliser.indexClauses(probe.primary, probe.clause_text);
    expect(
      index.clauses.map((clause) => ({
        text: clause.text,
        start: toCodePointOffset(probe.primary, clause.start),
        end: toCodePointOffset(probe.primary, clause.end),
      })),
    ).toEqual(probe.clauses);
  });

  it.each(document.cases)("folds homoglyphs and stacked marks identically: $id", (probe) => {
    expect(normaliser.foldConfusables(probe.text)).toBe(probe.confusables);
    expect(normaliser.stripCombining(probe.text)).toBe(probe.combining);
  });

  it.each(document.cases)("finds the same first-person subject: $id", (probe) => {
    expect(normaliser.hasFirstPerson(probe.primary)).toBe(probe.first_person);
  });

  it.each(document.cases)("splits quotations identically: $id", (probe) => {
    expect(normaliser.splitQuotes(probe.text)).toEqual(probe.quotes);
  });

  it.each(document.cases)("counts code points the way Python does: $id", (probe) => {
    // `text.length` is UTF-16 units; `len(text)` in Python is code points. Both are
    // recorded, and both must match — the input cap and the windowing depend on it.
    expect(codePointLength(probe.text)).toBe(probe.lengths.codepoints);
    expect(probe.text.length).toBe(probe.lengths.utf16_units);
    const message = normaliser.normalise(probe.text);
    expect(codePointLength(message.primary)).toBe(probe.lengths.primary);
    expect(codePointLength(normaliser.toClauseText(probe.stripped))).toBe(
      probe.lengths.clause_text,
    );
  });

  it.each(document.cases)("keeps clause offsets valid in the projection: $id", (probe) => {
    // The invariant `index_clauses` is built on: the clause text is the same length
    // as the primary, so an offset found in one is a valid offset in the other.
    const clauseText = normaliser.toClauseText(probe.stripped);
    expect(clauseText.length).toBe(normaliser.toPrimary(probe.stripped).length);
  });

  it("agrees on truncation at the input cap", () => {
    const cap = patterns.engine.max_input_chars;
    const long = "i want to die ".repeat(Math.ceil((cap + 10) / 14));
    const message = normaliser.normalise(long);
    expect(message.truncated).toBe(true);
    expect(codePointLength(message.primary)).toBeLessThanOrEqual(cap);
    expect(normaliser.normalise("short").truncated).toBe(false);
  });
});
