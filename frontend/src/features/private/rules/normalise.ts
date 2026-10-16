/**
 * Normalisation and pragmatics — the TypeScript port of
 * `backend/app/services/safety/normalise.py`.
 *
 * A rules engine over free text lives or dies on what it does *before* matching,
 * so this file is a faithful port rather than a reimplementation: the same
 * projections, in the same order, with the same constants (which come from the
 * bundle, i.e. from Python). Where JavaScript and Python disagree about strings —
 * whitespace, case folding, code points versus UTF-16 units — the difference is
 * handled in `pyStrings.ts` and named here.
 *
 * The pipeline ({@link Normaliser.normalise}):
 *
 * 1. invisible characters out (zero-width joiners, soft hyphens, BOM);
 * 2. apostrophes deleted, not spaced, then case folded;
 * 3. punctuation becomes a space, except Indic combining marks;
 * 4. five projections are produced, deduplicated, and *all* of them searched:
 *    plain, leet-substituted, spelling-corrected, repeated-letters-collapsed and
 *    homoglyph-folded — plus two more for phrase rules (whitespace stripped and
 *    letters collapsed).
 *
 * Then the pragmatics, judged per hit rather than per message: clauses, negation
 * (before, after and inside a hit, because the languages differ), figurative
 * speech on positive evidence only, and quotation/third-person flags that reshape
 * a reply without ever silencing one.
 */

import type { CompiledContext, CompiledPatternSet, EngineData } from "./bundleSchema";
import {
  collapseRepeats,
  compileTranslated,
  escapeLiteral,
  marksBody,
  type CompiledMatcher,
} from "./pyRegex";
import {
  codePointLength,
  codePointsToClassBody,
  isAlpha,
  makeCasefold,
  pySplit,
  pyStrip,
  sliceCodePoints,
  stripInvisible,
  stripLatinCombining,
  whitespaceRunRegex,
  wordClassBody,
} from "./pyStrings";
import type { UnicodeTables } from "./bundleSchema";

/** One stretch of text that should be read as a single statement. */
export interface Clause {
  text: string;
  start: number;
  end: number;
}

/** A text and its clauses, with offsets that are valid *in that text*. */
export interface ClauseIndex {
  text: string;
  clauses: Clause[];
  /** The clause containing `position` (falls back to the first). */
  clauseAt(position: number): Clause;
}

/** The projections of one input that the engine searches. */
export interface NormalisedMessage {
  /** Punctuation-spaced, apostrophe-free, case-folded text. */
  primary: string;
  /** Everything to search: `primary` plus the leet, spelling and folding variants. */
  variants: string[];
  /** `primary` with all whitespace removed (fused-spelling matching). */
  squashed: string;
  /** `primary` with repeated letters collapsed (`diiiie` matching). */
  collapsed: string;
  /** Clause boundaries for `primary`, at the same offsets. */
  index: ClauseIndex;
  /** True when the input was longer than the cap and has been cut. */
  truncated: boolean;
  /** Length of the original input, in code points. Metadata, not content. */
  charLength: number;
  /** The clause index for one variant. */
  indexFor(variant: string): ClauseIndex;
}

function makeClauseIndex(text: string, clauses: Clause[]): ClauseIndex {
  return {
    text,
    clauses,
    clauseAt(position: number): Clause {
      for (const clause of clauses) {
        if (clause.start <= position && position < clause.end) {
          return clause;
        }
      }
      return clauses[0] as Clause;
    },
  };
}

/**
 * The normaliser, compiled once per bundle.
 *
 * Every regular expression here is built from the bundle's Unicode tables, so the
 * classes mean what Python's mean: see `pyStrings.ts` for the three that differ
 * from JavaScript's defaults.
 */
export class Normaliser {
  readonly tables: UnicodeTables;
  readonly engine: EngineData;
  private readonly context: CompiledContext;
  private readonly casefold: (text: string) => string;
  private readonly punctuation: RegExp;
  private readonly punctuationKeepingBar: RegExp;
  private readonly clausePunctuation: RegExp;
  private readonly whitespaceRun: RegExp;
  private readonly separators: RegExp;
  private readonly clauseWords: CompiledMatcher;
  private readonly quotes: RegExp;
  private readonly barPattern: RegExp;
  private readonly tokenRun: RegExp;
  private readonly confusables: Map<string, string>;
  private readonly apostrophes: Set<string>;

  constructor(patterns: CompiledPatternSet) {
    this.tables = patterns.tables;
    this.engine = patterns.engine;
    this.context = patterns.context;
    this.casefold = makeCasefold(patterns.tables);

    const whitespace = codePointsToClassBody(patterns.tables.whitespace_codepoints);
    const word = wordClassBody(patterns.tables);
    const marks = marksBody(patterns.tables);
    const bar = escapeLiteral(patterns.engine.clause_bar);
    this.punctuation = new RegExp(`[^${word}${whitespace}${marks}]`, "gu");
    this.punctuationKeepingBar = new RegExp(`[^${word}${whitespace}${bar}${marks}]`, "gu");
    this.clausePunctuation = /[,;:!?.\n\r\t]+/gu;
    this.whitespaceRun = whitespaceRunRegex(patterns.tables);
    this.separators = new RegExp(`[${whitespace}${bar}]+`, "gu");
    this.barPattern = new RegExp(bar, "gu");
    this.tokenRun = new RegExp(`[^${whitespace}]+`, "gu");
    this.clauseWords = compileTranslated(
      "\\b(?:and|but|though|although|because|so|while|whereas|except|however|anyway)\\b",
      patterns.tables,
    );
    // `normalise._QUOTES`. A single-quoted span must contain a space, and a single
    // quote only opens or closes one when it is not inside a word — otherwise the
    // apostrophes of "im" and "dont" would pair up as a quotation (Day 29,
    // RT-S-QT-04).
    const body = `(?:[^']|(?<=[${word}])'(?=[${word}]))`;
    this.quotes = new RegExp(
      `"(?:[^"]{2,})"|\u201c(?:[^\u201d]{2,})\u201d|(?<![${word}])'(?:${body}*[${whitespace}]${body}*)'(?![${word}])`,
      "gu",
    );
    this.confusables = new Map(
      Object.entries(patterns.tables.confusables).map(([codePoint, target]) => [
        String.fromCodePoint(Number(codePoint)),
        target,
      ]),
    );
    this.apostrophes = new Set(
      patterns.tables.apostrophe_codepoints.map((codePoint) => String.fromCodePoint(codePoint)),
    );
  }

  /** `strip_invisible`: NFKC, then delete the zero-width and formatting characters. */
  strip(text: string): string {
    return stripInvisible(this.tables, text);
  }

  /** `normalise._fold`: apostrophes out, case folded. */
  private fold(text: string): string {
    let out = "";
    for (const char of text) {
      if (!this.apostrophes.has(char)) {
        out += char;
      }
    }
    return this.casefold(out);
  }

  /** `to_primary`: case-fold, drop apostrophes, punctuation to spaces, collapse. */
  toPrimary(text: string): string {
    const spaced = this.fold(text).replace(this.punctuation, " ");
    return pyStrip(this.tables, spaced.replace(this.whitespaceRun, " "));
  }

  /**
   * `to_clause_text`: like {@link toPrimary}, but sentence punctuation becomes a
   * clause bar. The result is the same length as `toPrimary` for the same input —
   * every run of punctuation or whitespace collapses to exactly one character in
   * both — so a match offset found in one is valid in the other.
   */
  toClauseText(text: string): string {
    const folded = this.fold(text);
    const marked = folded.replace(this.clausePunctuation, this.engine.clause_bar);
    const spaced = marked.replace(this.punctuationKeepingBar, " ");
    const collapsed = spaced.replace(this.separators, (match) =>
      match.includes(this.engine.clause_bar) ? this.engine.clause_bar : " ",
    );
    return pyStrip(this.tables, collapsed, `${this.engine.clause_bar} `);
  }

  /** `apply_leet`: `k1ll` becomes `kill`, one code point at a time. */
  applyLeet(text: string): string {
    let out = "";
    for (const char of text) {
      out += this.context.leetMap.get(char) ?? char;
    }
    return out;
  }

  /** `correct_spellings`: the curated map, longest source first. */
  correctSpellings(primary: string): string {
    let corrected = primary;
    for (const [pattern, replacement] of this.context.misspellings) {
      corrected = corrected.replace(pattern, () => replacement);
    }
    return corrected;
  }

  /** `squash`: remove every space — the projection fused spellings are matched against. */
  squash(text: string): string {
    return text.replace(this.whitespaceRun, "");
  }

  /** `collapse_repeats`: `diiiie` becomes `die`, on both sides of the comparison. */
  collapse(value: string): string {
    return collapseRepeats(this.tables, value);
  }

  /** `strip_latin_combining`: `d̶i̶e̶` and Zalgo read as `die`. */
  stripCombining(text: string): string {
    return stripLatinCombining(this.tables, text);
  }

  /**
   * `fold_confusables`: map Cyrillic and Greek look-alikes to Latin, but only
   * inside a token that is pretending — every letter in it must be Latin or a
   * look-alike, so a real word in another script is left alone.
   */
  foldConfusables(text: string): string {
    return text.replace(this.tokenRun, (token) => {
      const letters = [...token].filter(isAlpha);
      if (letters.length === 0) {
        return token;
      }
      const foldedLetters = letters.map((char) => this.casefold(char));
      if (!foldedLetters.some((char) => this.confusables.has(char))) {
        return token;
      }
      const latin = (char: string): boolean => char >= "a" && char <= "z";
      if (!foldedLetters.every((char) => this.confusables.has(char) || latin(char))) {
        return token;
      }
      let out = "";
      for (const char of token) {
        const folded = this.casefold(char);
        out += this.confusables.get(folded) ?? char;
      }
      return out;
    });
  }

  /**
   * `index_clauses`: split `text` into clauses and record their offsets.
   *
   * `marked` is {@link toClauseText} output for the same text; when given, its bars
   * are clause boundaries too. Clause text always comes from `text`, so tokens are
   * plain words.
   */
  indexClauses(text: string, marked?: string): ClauseIndex {
    if (text === "") {
      return makeClauseIndex("", [{ text: "", start: 0, end: 0 }]);
    }

    const cuts: [number, number][] = [];
    if (marked !== undefined) {
      for (const match of marked.matchAll(this.barPattern)) {
        cuts.push([match.index ?? 0, (match.index ?? 0) + match[0].length]);
      }
    }
    for (const match of this.clauseWords.find(text)) {
      cuts.push([match.index ?? 0, (match.index ?? 0) + match[0].length]);
    }
    cuts.sort((left, right) => left[0] - right[0] || left[1] - right[1]);

    const boundaries: number[] = [0];
    for (const [start, end] of cuts) {
      const last = boundaries[boundaries.length - 1] as number;
      if (start > last) {
        boundaries.push(start);
      }
      boundaries.push(end);
    }
    boundaries.push(text.length);

    const clauses: Clause[] = [];
    for (let index = 0; index < boundaries.length - 1; index += 1) {
      const left = boundaries[index] as number;
      const right = boundaries[index + 1] as number;
      const chunk = text.slice(left, right);
      const stripped = pyStrip(this.tables, chunk);
      if (stripped === "") {
        continue;
      }
      const first = [...stripped][0] as string;
      const offset = left + chunk.indexOf(first);
      clauses.push({ text: stripped, start: offset, end: offset + stripped.length });
    }
    if (clauses.length === 0) {
      clauses.push({ text, start: 0, end: text.length });
    }
    return makeClauseIndex(text, clauses);
  }

  /**
   * `is_negated`: true when a negation cancels the hit inside this clause.
   *
   * Three directions, because the languages differ: *before* the hit for English
   * and Hindi ("i dont want to die"), *after* it only for patterns tagged with a
   * language, because Bengali negates post-verbally ("ami bachte chai na"), and
   * *inside* it for the same reason — Hindi puts the cue between the verb stem and
   * its auxiliary ("main marna nahi chahta").
   *
   * `cant` and `cannot` are deliberately not cues: inability is not absence.
   */
  isNegated(
    clause: Clause,
    start: number,
    end: number,
    options: { language?: string | null; negatesItself?: boolean } = {},
  ): boolean {
    const vocabulary = this.context.vocabulary;
    const localStart = Math.max(0, start - clause.start);
    const localEnd = Math.max(0, end - clause.start);
    const backward = pySplit(this.tables, clause.text.slice(0, localStart)).slice(
      -this.engine.max_backward_tokens,
    );

    let skipped = 0;
    for (let index = backward.length - 1; index >= 0; index -= 1) {
      const token = backward[index] as string;
      if (this.context.transparentWords.has(token)) {
        skipped += 1;
        if (skipped > this.engine.max_transparent_skips) {
          break;
        }
        continue;
      }
      if (this.context.negationCues.has(token)) {
        return true;
      }
      // First content word and it is not a cue: stop looking backwards. A language
      // that negates later in the phrase still has its say below.
      break;
    }

    const language = options.language ?? null;
    if (language !== null && language !== "") {
      if (options.negatesItself !== true) {
        const inside = pySplit(this.tables, clause.text.slice(localStart, localEnd));
        const cues = new Set([...vocabulary.negation_cues, ...vocabulary.negation_after_cues]);
        if (inside.some((token) => cues.has(token))) {
          return true;
        }
      }
      const suffix = pySplit(this.tables, clause.text.slice(localEnd)).slice(
        0,
        this.engine.max_forward_tokens,
      );
      for (const token of suffix) {
        if (this.context.transparentWords.has(token)) {
          continue;
        }
        return this.context.negationAfterCues.has(token);
      }
    }
    return false;
  }

  /**
   * `looks_figurative`: true only on positive evidence that the wording is
   * exaggeration — a benign frame from the data ("dying to know"), or an inanimate
   * subject among the last few words before the hit ("this traffic is killing me").
   * Suspicion is never enough to drop a hit.
   */
  looksFigurative(clause: Clause, start: number, subjectEvidence: boolean): boolean {
    if (this.context.benignFrames.some((frame) => frame.test(clause.text))) {
      return true;
    }
    if (!subjectEvidence) {
      return false;
    }
    const tokens = pySplit(
      this.tables,
      clause.text.slice(0, Math.max(0, start - clause.start)),
    ).slice(-this.engine.subject_window);
    if (tokens.length === 0) {
      return false;
    }
    return this.context.benignSubjectPattern.test(tokens.join(" "));
  }

  /**
   * `has_first_person`: true when `text` has a first-person subject that really is
   * one. Possessive-relative phrases are blanked out first, so "my friend says she
   * wants to die" is not about the speaker while "i want to die, my mum doesnt
   * care" still is.
   */
  hasFirstPerson(text: string): boolean {
    const stripped = this.context.relationPattern.replace(text, " ");
    return this.context.firstPersonPattern.test(stripped);
  }

  /**
   * `split_quotes`: split raw text into (outside the quotes, inside the quotes).
   *
   * Used to answer one question: does the risky wording exist *only* inside a
   * quotation? If it does, the speaker is reporting somebody else's words.
   */
  splitQuotes(raw: string): [string, string] {
    const quoted: string[] = [];
    const remainder = raw.replace(this.quotes, (match) => {
      quoted.push(match);
      return " ";
    });
    return [remainder, quoted.join(" ")];
  }

  /** `normalise`: produce every projection of `text` that the engine will search. */
  normalise(text: string, maxChars = this.engine.max_input_chars): NormalisedMessage {
    const charLength = codePointLength(text);
    const truncated = charLength > maxChars;
    const working = truncated ? sliceCodePoints(text, 0, maxChars) : text;

    const cleaned = this.strip(working);
    const primary = this.toPrimary(cleaned);
    const leetVariant = this.toPrimary(this.applyLeet(cleaned));
    const corrected = this.correctSpellings(primary);
    // Stretched letters *and* a typo in the same message ("kiiiillll meee") need
    // both fixes, in that order: collapsing first turns the stretch into a word the
    // spelling map recognises.
    const collapsedVariant = this.correctSpellings(this.collapse(primary));
    // Homoglyphs and stacked diacritics: one extra variant, spelling corrected so a
    // homoglyph "suicide" with a typo is covered. The primary keeps the person's
    // real characters.
    const folded = this.toPrimary(this.foldConfusables(this.stripCombining(cleaned)));
    const foldedVariant = this.correctSpellings(folded);

    const variants: string[] = [];
    for (const candidate of [primary, leetVariant, corrected, collapsedVariant, foldedVariant]) {
      if (candidate !== "" && !variants.includes(candidate)) {
        variants.push(candidate);
      }
    }

    const index = this.indexClauses(primary, this.toClauseText(cleaned));
    return {
      primary,
      variants,
      squashed: this.squash(primary),
      collapsed: this.collapse(primary),
      index,
      truncated,
      charLength,
      indexFor: (variant: string): ClauseIndex =>
        variant === primary ? index : this.indexClauses(variant),
    };
  }
}
