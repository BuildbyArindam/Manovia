/**
 * The output guard, on the device — the port of
 * `backend/app/services/safety/output_guard.py`.
 *
 * AGENTS.md safety rule 4: every model output passes an output-safety check before
 * it is shown. In Private Mode the model *is* on the device, so the check has to be
 * too — and it has to be the same check. The rules come from the bundle
 * (`output_patterns.yaml`, compiled), the categories are the same closed vocabulary,
 * and the fallback copy is the same pre-written safe reply.
 *
 * A rejection exposes category codes only: never the matched text, never a snippet,
 * never a hash of the reply. That is what makes the verdict safe to log, and the
 * reason `GuardVerdict` has no evidence field.
 *
 * One deliberate reduction, stated plainly: the server's `wrong_language` check also
 * runs statistical language detection (`langdetect`). There is no offline equivalent
 * in a browser, so this port checks the *script* — an English reply written in
 * Devanagari or Bengali, a Hindi reply in Bengali script, and so on — and accepts
 * everything else. Romanised Hindi is accepted as Hindi on both sides, because users
 * code-switch and the prompt allows that register.
 */

import type { RiskLevel } from "@/lib/endpoints";

import type { CompiledMatcher } from "./pyRegex";
import { escapeLiteral, translatePythonRegex } from "./pyRegex";
import { codePointsToClassBody, pySplit } from "./pyStrings";
import type { CompiledPatternSet, PrivateBundle } from "./bundleSchema";
import type { ClauseIndex } from "./normalise";
import { Normaliser } from "./normalise";

/** The server's `DEFAULT_MAX_WORDS`; Private Mode uses the same limit. */
export const DEFAULT_MAX_WORDS = 120;
/** `output_guard.PROMPT_ECHO_WORDS`. */
export const PROMPT_ECHO_WORDS = 8;
/** `output_guard.PROMPT_ECHO_MIN_COVERED`. */
export const PROMPT_ECHO_MIN_COVERED = 20;

const SUPPORTED_LOCALES: readonly string[] = ["en", "hi", "bn"];
const PLACEHOLDER = /\{[a-z_]+\}/g;

/**
 * Build the guard the way Private Mode runs it: shingles from the prompts that ship
 * in the bundle, the same `content/prompts/system_v*.md` files Python reads.
 *
 * A guard built without prompt text would still catch every other category, but its
 * prompt-echo check would be vacuous — and the Day 29 red team found that leak on the
 * server side (RT-I-SP-01..03), so it is not a check to leave disabled.
 */
export function buildOutputGuard(
  bundle: PrivateBundle,
  patterns: CompiledPatternSet,
  options: Omit<GuardOptions, "promptTexts"> = {},
): RuleBasedOutputGuard {
  const promptTexts = Object.keys(bundle.prompts)
    .sort()
    .map((key) => bundle.prompts[key]?.text ?? "");
  return new RuleBasedOutputGuard(bundle, patterns, { ...options, promptTexts });
}

/** `output_guard._locale`. */
export function guardLocale(language?: string | null): string {
  const code = (language ?? "en").split("-", 1)[0]?.trim().toLowerCase() ?? "en";
  return SUPPORTED_LOCALES.includes(code) ? code : "en";
}

/** The guard's decision about one reply. It contains no prose evidence. */
export interface GuardVerdict {
  text: string;
  passed: boolean;
  /** The first failing category, or `null` when the reply passed. */
  reason: string | null;
  categories: string[];
}

export interface GuardOptions {
  /** Replies longer than this are refused. The server's limit is the default. */
  maxWords?: number;
  /**
   * The prompt text the on-device model was given. Shingles are computed from it so
   * a reply that recites its own instructions is caught — the Day 29 red team found
   * this on the server side (RT-I-SP-01..03) and it applies here unchanged.
   */
  promptTexts?: string[];
}

/** `nlp.script_of`: which script a string is written in. */
export type Script = "devanagari" | "bengali" | "latin" | "none";

const DEVANAGARI = /[\u0900-\u097f]/gu;
const BENGALI = /[\u0980-\u09ff]/gu;

export function scriptOf(text: string): Script {
  if (text.trim() === "") {
    return "none";
  }
  const devanagari = [...text.matchAll(DEVANAGARI)].length;
  const bengali = [...text.matchAll(BENGALI)].length;
  let letters = 0;
  for (const char of text) {
    if (/^\p{L}$/u.test(char)) {
      letters += 1;
    }
  }
  if (letters === 0) {
    return "none";
  }
  if (devanagari / letters >= 0.3) {
    return "devanagari";
  }
  if (bengali / letters >= 0.3) {
    return "bengali";
  }
  return "latin";
}

/** `output_guard.load_prompt_shingles`, over whatever prompt this client used. */
export function promptShingles(
  texts: readonly string[],
  normaliser: Normaliser,
  size: number = PROMPT_ECHO_WORDS,
): Set<string> {
  const shingles = new Set<string>();
  for (const text of texts) {
    for (const segment of text.split(PLACEHOLDER)) {
      const tokens = pySplit(normaliser.tables, normaliser.toPrimary(segment));
      for (let index = 0; index + size <= tokens.length; index += 1) {
        shingles.add(tokens.slice(index, index + size).join("\u0000"));
      }
    }
  }
  return shingles;
}

/** `output_guard.echoes_prompt`: coverage, not one lucky run. */
export function echoesPrompt(
  normalized: string,
  shingles: ReadonlySet<string>,
  tables: CompiledPatternSet["tables"],
  size: number = PROMPT_ECHO_WORDS,
  minCovered: number = PROMPT_ECHO_MIN_COVERED,
): boolean {
  const tokens = pySplit(tables, normalized);
  const covered = new Set<number>();
  for (let index = 0; index + size <= tokens.length; index += 1) {
    if (shingles.has(tokens.slice(index, index + size).join("\u0000"))) {
      for (let offset = index; offset < index + size; offset += 1) {
        covered.add(offset);
      }
    }
  }
  return covered.size >= minCovered;
}

interface CompiledOutputRule {
  id: string;
  category: string;
  matcher: CompiledMatcher;
  negationSensitive: boolean;
}

/**
 * Deterministic output rules, evaluated before any text reaches the screen.
 *
 * Build one per bundle (the matchers are compiled once) and call {@link check} with
 * the whole reply: `requiresFullText` is true, because "does this reply promise a
 * cure" is not a question that can be answered sentence by sentence.
 */
export class RuleBasedOutputGuard {
  readonly requiresFullText = true;
  readonly maxWords: number;
  private readonly normaliser: Normaliser;
  private readonly rules: CompiledOutputRule[];
  private readonly checkInPatterns: CompiledMatcher[];
  private readonly negationCues: string[][];
  private readonly safeReplies: Record<string, string>;
  private readonly trimmedClosings: Record<string, string>;
  private readonly shingles: Set<string>;
  private readonly levels: Record<RiskLevel, number>;

  constructor(bundle: PrivateBundle, patterns: CompiledPatternSet, options: GuardOptions = {}) {
    const maxWords = options.maxWords ?? DEFAULT_MAX_WORDS;
    if (maxWords < 1) {
      throw new Error("maxWords must be at least 1");
    }
    this.maxWords = maxWords;
    this.normaliser = new Normaliser(patterns);
    this.levels = patterns.engine.levels;
    this.safeReplies = bundle.output_guard.safe_replies;
    this.trimmedClosings = bundle.output_guard.trimmed_closings;

    const whitespace = `[${codePointsToClassBody(patterns.tables.whitespace_codepoints)}]+`;
    // `output_guard._compile_phrase`. Note the boundary class is ASCII `[a-z0-9_]`,
    // not the input engine's Unicode `\w`: the output rules were written and tested
    // against that, and widening it here would change what they match.
    const compilePhrase = (phrase: string, indic: boolean): CompiledMatcher => {
      const normalized = this.normaliser.toPrimary(phrase);
      if (normalized === "") {
        throw new Error("output phrase must not be empty");
      }
      const literal = normalized
        .split(/([ \t\n\r\f\v]+)/)
        .filter((part) => part !== "")
        .map((part) => (/^[ \t\n\r\f\v]+$/.test(part) ? whitespace : escapeLiteral(part)))
        .join("");
      const source = indic ? literal : `(?<![a-z0-9_])${literal}(?![a-z0-9_])`;
      return compiled(source);
    };
    const compiled = (source: string): CompiledMatcher => {
      const single = new RegExp(source, "iu");
      const global = new RegExp(source, "iug");
      return {
        test: (text: string): boolean => single.test(text),
        find: (text: string): RegExpMatchArray[] => [...text.matchAll(global)],
        replace: (text: string, replacement: string): string =>
          text.replace(global, () => replacement),
        source,
      };
    };

    this.rules = bundle.output_guard.rules.map((rule) => ({
      id: rule.id,
      category: rule.category,
      negationSensitive: rule.negation_sensitive,
      matcher:
        rule.kind === "phrase"
          ? compilePhrase(rule.value, rule.script === "indic")
          : // Python strips YAML's folded-block whitespace from an output regex
            // before compiling it; the bundle ships the value as written.
            compiled(translatePythonRegex(rule.value.replace(/\s+/g, ""), patterns.tables)),
    }));

    this.checkInPatterns = bundle.output_guard.check_in_phrases.map((phrase) => {
      let indic = false;
      for (const char of phrase) {
        if ((char.codePointAt(0) ?? 0) > 0x7f) {
          indic = true;
          break;
        }
      }
      return compilePhrase(phrase, indic);
    });

    // Longest cue first, as Python does, so a two-word cue is not shadowed.
    this.negationCues = [...bundle.output_guard.negation_cues]
      .sort((left, right) => right.length - left.length)
      .map((cue) => pySplit(patterns.tables, this.normaliser.toPrimary(cue)))
      .filter((tokens) => tokens.length > 0);

    this.shingles = promptShingles(options.promptTexts ?? [], this.normaliser);
  }

  /** The pre-written local reply for a second failure, or a guard outage. */
  safeReply(language?: string | null): string {
    const reply = this.safeReplies[guardLocale(language)];
    if (reply === undefined) {
      throw new Error(`no safe reply for locale ${guardLocale(language)}`);
    }
    return reply;
  }

  /** The closing line appended when a reply had to be shortened. */
  trimmedClosing(language?: string | null): string {
    return this.trimmedClosings[guardLocale(language)] ?? "";
  }

  /**
   * `output_guard._is_negated`: does a cue sit in the eight tokens before the hit?
   *
   * The cues are token *sequences*, matched against the tail of the prefix, so
   * "i would never" counts and "never" inside a longer phrase does not accidentally
   * cancel something else.
   */
  private isNegated(text: string, position: number, index: ClauseIndex): boolean {
    const clause = index.clauseAt(position);
    const prefix = pySplit(this.normaliser.tables, text.slice(clause.start, position)).slice(-8);
    return this.negationCues.some(
      (cue) =>
        cue.length <= prefix.length &&
        cue.every((token, offset) => prefix[prefix.length - cue.length + offset] === token),
    );
  }

  /**
   * `output_guard._language_mismatch`, minus the statistical detector (see the
   * module comment): script disagreement is refused, everything else is accepted.
   */
  private languageMismatch(text: string, expectedLanguage: string): boolean {
    const expected = guardLocale(expectedLanguage);
    const script = scriptOf(text);
    if (expected === "en" && (script === "devanagari" || script === "bengali")) {
      return true;
    }
    if (expected === "hi" && script === "bengali") {
      return true;
    }
    return expected === "bn" && script === "devanagari";
  }

  /**
   * Return the input only when it satisfies every deterministic policy.
   *
   * `risk` is the level the *user's* message was assessed at: a reply to a
   * MEDIUM-or-worse message must check in with the person, so a reply that does not
   * is refused however harmless it reads.
   */
  /**
   * True when this text already carries one of the shipped check-in phrases.
   *
   * The guard refuses a MEDIUM-or-worse reply that never checks in, and the
   * template reply writer (models/templateReply.ts) has to satisfy that rule in
   * whichever locale it is writing. Rather than hard-coding an English question, it
   * asks the guard: the phrase list is bundle data and already translated.
   */
  hasCheckIn(text: string): boolean {
    const normalized = this.normaliser.toPrimary(this.normaliser.strip(text));
    return this.checkInPatterns.some((pattern) => pattern.test(normalized));
  }

  check(
    text: string,
    options: { risk: RiskLevel; expectedLanguage?: string | null },
  ): GuardVerdict {
    const tables = this.normaliser.tables;
    const cleaned = this.normaliser.strip(text);
    const normalized = this.normaliser.toPrimary(cleaned);
    const index = this.normaliser.indexClauses(normalized, this.normaliser.toClauseText(cleaned));
    const categories: string[] = [];
    const add = (category: string): void => {
      if (!categories.includes(category)) {
        categories.push(category);
      }
    };

    for (const rule of this.rules) {
      const match = rule.matcher.find(normalized)[0];
      if (match !== undefined) {
        const negated =
          rule.negationSensitive && this.isNegated(normalized, match.index ?? 0, index);
        if (!negated) {
          add(rule.category);
        }
      }
    }

    if (echoesPrompt(normalized, this.shingles, tables)) {
      add("prompt_leak");
    }
    if (pySplit(tables, normalized).length > this.maxWords) {
      add("too_long");
    }
    if (options.expectedLanguage !== null && options.expectedLanguage !== undefined) {
      if (this.languageMismatch(cleaned, options.expectedLanguage)) {
        add("wrong_language");
      }
    }
    if (
      this.levels[options.risk] >= this.levels.medium &&
      !this.checkInPatterns.some((pattern) => pattern.test(normalized))
    ) {
      add("missing_check_in");
    }

    if (categories.length === 0) {
      return { text, passed: true, reason: null, categories: [] };
    }
    return {
      text: this.safeReply(options.expectedLanguage),
      passed: false,
      reason: categories[0] ?? null,
      categories,
    };
  }
}
