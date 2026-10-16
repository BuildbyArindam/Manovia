/**
 * The safety rules engine — the TypeScript port of
 * `backend/app/services/safety/rules.py`.
 *
 * This is the module AGENTS.md safety rule 1 depends on, running on the device:
 * crisis detection before any model call, deterministic, never improvising. In
 * Private Mode there is no server to defer to, so the port has to be faithful
 * rather than approximate — `parity.test.ts` holds it to 500 phrases assessed
 * identically by both engines, levels, categories, context flags and rationale
 * codes.
 *
 * **Conservative by construction**, exactly as the Python is:
 *
 * - a hit is dropped only on *positive* evidence — a negation attached to it, a
 *   benign frame or an inanimate subject next to it, quotation marks around it;
 * - if a rule fires anywhere, in any spelling variant, once, it counts;
 * - a third-person or quoted message is *reshaped*, never silenced: the cap is
 *   HIGH, and IMMINENT survives only for an acute medical emergency;
 * - negated ideation is still LOW, not NONE.
 *
 * **Privacy.** The engine reads text and returns metadata: a level, categories,
 * stable pattern ids and counters. It stores nothing and logs nothing, and no
 * rationale code can carry a fragment of somebody's message (the ids come from
 * the bundle, which validates their shape).
 */

import type {
  CompiledPatternSet,
  CompiledRule,
  RationaleCodes,
  RiskCategory,
  RiskLevel,
} from "./bundleSchema";
import { compileTranslated, type CompiledMatcher } from "./pyRegex";
import { codePointLength, pySplit, pyStrip, sliceCodePoints } from "./pyStrings";
import type { Clause, ClauseIndex, NormalisedMessage } from "./normalise";
import { Normaliser } from "./normalise";

/** `base.AssessmentContext`: pragmatic facts about the message, never its content. */
export interface AssessmentContext {
  first_person: boolean;
  third_person: boolean;
  quoted: boolean;
  fiction_frame: boolean;
  timeframe: boolean;
  negated_hits: number;
  figurative_hits: number;
  language: string | null;
  truncated: boolean;
  variants_searched: number;
}

/** `base.RiskAssessment`: what the engine decided, safe to log and to store. */
export interface RiskAssessment {
  level: RiskLevel;
  matched_categories: RiskCategory[];
  rationale_codes: string[];
  context: AssessmentContext;
}

/** One place where one rule matched one projection of the message. */
interface Occurrence {
  rule: CompiledRule;
  clause: Clause;
  start: number;
  end: number;
  negated: boolean;
  figurative: boolean;
}

/** The pragmatic judgements the engine makes about one message, made once. */
interface MessageFlags {
  first_person: boolean;
  third_person: boolean;
  quoted_only: boolean;
  fiction_frame: boolean;
  timeframe: boolean;
}

function emptyContext(language: string | null): AssessmentContext {
  return {
    first_person: false,
    third_person: false,
    quoted: false,
    fiction_frame: false,
    timeframe: false,
    negated_hits: 0,
    figurative_hits: 0,
    language,
    truncated: false,
    variants_searched: 0,
  };
}

/** `base.empty_assessment`. */
function emptyAssessment(language: string | null, rationale?: string): RiskAssessment {
  return {
    level: "none",
    matched_categories: [],
    rationale_codes: rationale === undefined ? [] : [rationale],
    context: emptyContext(language),
  };
}

/** `rules._clean_language`: normalise an optional caller hint, or drop it. */
function cleanLanguage(language: string | null | undefined): string | null {
  if (language === null || language === undefined) {
    return null;
  }
  const cleaned = language.trim().toLowerCase();
  return cleaned === "" ? null : cleaned;
}

export interface RuleEngineOptions {
  /** Override the input cap; the bundle's value is used when omitted. */
  maxChars?: number;
}

/**
 * Deterministic risk assessment over the compiled pattern set.
 *
 * Stateless: build one (or use {@link buildRuleEngine}, which caches) and call
 * {@link assess} as often as needed. Nothing is mutated and nothing is persisted,
 * so the same text always produces the same assessment.
 */
export class RuleEngine {
  readonly patterns: CompiledPatternSet;
  readonly maxChars: number;
  private readonly normaliser: Normaliser;
  private readonly selfQuote: CompiledMatcher;
  private readonly codes: RationaleCodes;

  constructor(patterns: CompiledPatternSet, options: RuleEngineOptions = {}) {
    const maxChars = options.maxChars ?? patterns.engine.max_input_chars;
    if (maxChars < 1) {
      throw new Error("maxChars must be at least 1");
    }
    this.patterns = patterns;
    this.maxChars = maxChars;
    this.normaliser = new Normaliser(patterns);
    this.selfQuote = compileTranslated(patterns.engine.self_quote_pattern, patterns.tables);
    this.codes = patterns.engine.codes;
  }

  /** Counts and provenance — no text, no pattern values. Mirrors `describe()`. */
  describe(): Record<string, unknown> {
    const byLevel: Record<string, number> = {};
    const byCategory: Record<string, number> = {};
    const byLanguage: Record<string, number> = {};
    for (const rule of this.patterns.rules) {
      byLevel[rule.data.level] = (byLevel[rule.data.level] ?? 0) + 1;
      byCategory[rule.data.category] = (byCategory[rule.data.category] ?? 0) + 1;
      const key = rule.data.language ?? "en";
      byLanguage[key] = (byLanguage[key] ?? 0) + 1;
    }
    const vocabulary = this.patterns.context.vocabulary;
    return {
      rules: this.patterns.rules.length,
      by_level: byLevel,
      by_category: byCategory,
      by_language: byLanguage,
      negation_cues: vocabulary.negation_cues.length,
      third_person_markers: vocabulary.third_person_markers.length,
      fiction_frames: vocabulary.fiction_frames.length,
      relation_nouns: vocabulary.relation_nouns.length,
      benign_frames: vocabulary.benign_frames.length,
      benign_subjects: vocabulary.benign_subjects.length,
      misspellings: vocabulary.misspellings.length,
      max_chars: this.maxChars,
    };
  }

  /**
   * Assess one message — all of it, up to `engine.max_windowed_chars`.
   *
   * `language` is an optional caller hint. It is recorded for reporting but never
   * used to route: every pattern runs against every message, so a mis-detected
   * language cannot hide a risk.
   *
   * A message longer than one window is read in overlapping windows (the overlap is
   * longer than any pattern, so a phrase on a boundary is whole in one of them) and
   * the highest-level window answers, tagged `input.windowed`.
   */
  assess(text: string, language?: string | null): RiskAssessment {
    if (codePointLength(text) <= this.maxChars) {
      return this.assessWindow(text, cleanLanguage(language));
    }
    return this.assessWindowed(text, cleanLanguage(language));
  }

  private assessWindowed(text: string, language: string | null): RiskAssessment {
    const engine = this.patterns.engine;
    const beyondCap = codePointLength(text) > engine.max_windowed_chars;
    const readable = sliceCodePoints(text, 0, engine.max_windowed_chars);
    const overlap = Math.min(engine.window_overlap_chars, Math.floor(this.maxChars / 2));
    const step = this.maxChars - overlap;
    let best: RiskAssessment | null = null;
    let start = 0;
    const readableLength = codePointLength(readable);
    for (;;) {
      const window = this.assessWindow(
        sliceCodePoints(readable, start, start + this.maxChars),
        language,
      );
      // Strictly greater: the earliest window wins a tie, so the answer is
      // deterministic and its context codes come from one coherent window.
      if (best === null || this.rank(window.level) > this.rank(best.level)) {
        best = window;
      }
      if (start + this.maxChars >= readableLength) {
        break;
      }
      start += step;
    }
    const chosen = best as RiskAssessment;
    const codes = chosen.rationale_codes.filter(
      (code) => code !== this.codes.empty_input && code !== this.codes.truncated,
    );
    codes.push(this.codes.windowed);
    if (beyondCap) {
      codes.push(this.codes.truncated);
    }
    return {
      ...chosen,
      rationale_codes: codes,
      context: { ...chosen.context, truncated: beyondCap },
    };
  }

  private rank(level: RiskLevel): number {
    return this.patterns.engine.levels[level];
  }

  private assessWindow(text: string, language: string | null): RiskAssessment {
    const message = this.normaliser.normalise(text, this.maxChars);
    if (message.primary === "") {
      return emptyAssessment(
        language,
        message.truncated ? this.codes.truncated : this.codes.empty_input,
      );
    }

    const occurrences = this.match(message);
    const filtered = this.filter(occurrences);
    // Every pragmatic judgement is made once, here, so the level, the cap and the
    // reported context cannot disagree with each other.
    const flags = this.buildFlags(text, message, occurrences, filtered.accepted);

    const categories = [...new Set(filtered.accepted.map((hit) => hit.rule.data.category))].sort(
      (left, right) => (left < right ? -1 : left > right ? 1 : 0),
    );
    const capped = this.capForThirdPerson(
      this.levelFor({
        accepted: filtered.accepted,
        categories,
        negatedCount: filtered.negated,
        thirdPerson: flags.third_person,
        timeframe: flags.timeframe,
      }),
      categories,
      flags.third_person,
    );

    const rationale = [...new Set(filtered.accepted.map((hit) => hit.rule.data.id))].sort();
    if (filtered.negated > 0) {
      rationale.push(this.codes.negated);
    }
    if (filtered.figurative > 0) {
      rationale.push(this.codes.figurative);
    }
    if (flags.third_person) {
      rationale.push(this.codes.third_person);
    }
    if (flags.quoted_only) {
      rationale.push(this.codes.quoted);
    }
    if (flags.fiction_frame) {
      rationale.push(this.codes.fiction_frame);
    }
    if (flags.timeframe) {
      rationale.push(this.codes.timeframe);
    }
    if (message.truncated) {
      rationale.push(this.codes.truncated);
    }

    return {
      level: capped,
      matched_categories: categories,
      rationale_codes: rationale,
      context: {
        first_person: flags.first_person,
        third_person: flags.third_person,
        quoted: flags.quoted_only,
        fiction_frame: flags.fiction_frame,
        timeframe: flags.timeframe,
        negated_hits: filtered.negated,
        figurative_hits: filtered.figurative,
        language,
        truncated: message.truncated,
        variants_searched: message.variants.length,
      },
    };
  }

  /** Judge one match while its offsets are still valid for its projection. */
  private occurrence(
    rule: CompiledRule,
    index: ClauseIndex,
    start: number,
    end: number,
  ): Occurrence {
    const clause = index.clauseAt(start);
    return {
      rule,
      clause,
      start,
      end,
      negated:
        rule.data.negation_sensitive &&
        this.normaliser.isNegated(clause, start, end, {
          language: rule.data.language,
          negatesItself: this.patterns.selfNegating.has(rule.data.id),
        }),
      figurative:
        rule.data.figurative_prone &&
        this.normaliser.looksFigurative(clause, start, rule.data.figurative_subject),
    };
  }

  /**
   * Run every rule against every projection of the message.
   *
   * The honest text is judged first and on its own, because its verdicts bind every
   * derived view of it: a rule that was negated or judged figurative in the real
   * text stays that way everywhere. The derived views can add a hit; they can never
   * cancel one — otherwise obfuscating a sentence would make it *harder* to dismiss
   * than typing it plainly.
   */
  private match(message: NormalisedMessage): Occurrence[] {
    const occurrences: Occurrence[] = [];
    const rules = this.patterns.rules;
    const [primary, ...derivedVariants] = message.variants;
    if (primary === undefined) {
      return occurrences;
    }

    const primaryIndex = message.indexFor(primary);
    for (const rule of rules) {
      for (const match of rule.pattern.find(primary)) {
        occurrences.push(
          this.occurrence(
            rule,
            primaryIndex,
            match.index ?? 0,
            (match.index ?? 0) + match[0].length,
          ),
        );
      }
    }

    const negatedRules = new Set(
      occurrences.filter((hit) => hit.negated).map((hit) => hit.rule.data.id),
    );
    const figurativeRules = new Set(
      occurrences.filter((hit) => hit.figurative).map((hit) => hit.rule.data.id),
    );
    const dismissed = (id: string): boolean => negatedRules.has(id) || figurativeRules.has(id);

    for (const variant of derivedVariants) {
      const index = message.indexFor(variant);
      for (const rule of rules) {
        if (dismissed(rule.data.id)) {
          continue;
        }
        for (const match of rule.pattern.find(variant)) {
          occurrences.push(
            this.occurrence(rule, index, match.index ?? 0, (match.index ?? 0) + match[0].length),
          );
        }
      }
    }

    // Spacing- and letter-trick projections. Each is a different string, so clause
    // offsets from the primary are meaningless here: the projection is split into
    // its own clauses, and rules carry a purpose-built matcher for each.
    const projections: [string, "squashed" | "collapsed"][] = [
      [message.squashed, "squashed"],
      [message.collapsed, "collapsed"],
    ];
    for (const [projection, attribute] of projections) {
      if (projection === "") {
        continue;
      }
      const index = this.normaliser.indexClauses(projection);
      for (const rule of rules) {
        if (dismissed(rule.data.id)) {
          continue;
        }
        const compiled = rule[attribute];
        if (compiled === null) {
          continue;
        }
        for (const match of compiled.find(projection)) {
          occurrences.push(
            this.occurrence(rule, index, match.index ?? 0, (match.index ?? 0) + match[0].length),
          );
        }
      }
    }
    return occurrences;
  }

  /**
   * Drop negated hits, and figurative ones with positive benign evidence.
   *
   * Suppression is per *occurrence*, never per message: an inanimate subject
   * explains away the "killing me" in one clause and says nothing about the rest of
   * it. "this exam is killing me, i want to die" keeps its second half.
   */
  private filter(occurrences: Occurrence[]): {
    accepted: Occurrence[];
    negated: number;
    figurative: number;
  } {
    const accepted: Occurrence[] = [];
    const seen = new Set<string>();
    const negated = new Set<string>();
    const figurative = new Set<string>();

    for (const hit of occurrences) {
      if (hit.negated) {
        negated.add(hit.rule.data.id);
        continue;
      }
      if (hit.figurative) {
        figurative.add(hit.rule.data.id);
        continue;
      }
      if (seen.has(hit.rule.data.id)) {
        continue;
      }
      seen.add(hit.rule.data.id);
      accepted.push(hit);
    }
    return { accepted, negated: negated.size, figurative: figurative.size };
  }

  /**
   * True when the risky wording exists *only* inside quotation marks.
   *
   * Re-runs the matcher over the text with the quoted spans removed: if nothing
   * risky survives, the speaker was quoting somebody. The second pass is only paid
   * when the message contains quotes *and* something worth explaining.
   */
  private isQuotedOnly(
    text: string,
    message: NormalisedMessage,
    occurrences: Occurrence[],
  ): boolean {
    const levels = this.patterns.engine.levels;
    const risky = new Set(
      occurrences
        .filter((hit) => !hit.negated && levels[hit.rule.data.level] >= levels.medium)
        .map((hit) => hit.rule.data.id),
    );
    if (risky.size === 0) {
      return false;
    }
    const [remainder, quoted] = this.normaliser.splitQuotes(text);
    if (pyStrip(this.patterns.tables, quoted) === "") {
      return false;
    }
    const remainderMessage = this.normaliser.normalise(remainder, this.maxChars);
    if (remainderMessage.primary === message.primary) {
      return false;
    }
    const matchedOutside = new Set(
      this.match(remainderMessage)
        .filter((hit) => !hit.negated)
        .map((hit) => hit.rule.data.id),
    );
    for (const id of risky) {
      if (matchedOutside.has(id)) {
        return false;
      }
    }
    return true;
  }

  /**
   * True when the message reads as being about somebody else.
   *
   * Two signals, in decreasing order of trust: a subject+verb frame from the data
   * ("she said", "my friend wants", "usne kaha"), or a person marker *inside the
   * clause of an accepted hit* where that clause has no first-person subject. The
   * second is deliberately narrow: "i want to die, my mum doesnt care" is about the
   * speaker, and merely mentioning a relative must not downgrade it.
   */
  private isThirdPerson(message: NormalisedMessage, accepted: Occurrence[]): boolean {
    const context = this.patterns.context;
    const frameMatched = context.thirdPersonPatterns.some((pattern) =>
      pattern.test(message.primary),
    );
    if (frameMatched && !accepted.some((hit) => this.hitIsAboutTheSpeaker(hit))) {
      return true;
    }

    const clauses = new Set(accepted.map((hit) => message.index.clauseAt(hit.start).text));
    for (const clauseText of clauses) {
      if (this.normaliser.hasFirstPerson(clauseText)) {
        continue;
      }
      if (context.thirdPersonMarkerPattern.test(clauseText)) {
        return true;
      }
    }
    return false;
  }

  /**
   * True when the hit, or the one token right before it, is first person.
   *
   * Narrow on purpose: "i want to die" and "kill myself" qualify; "i think she
   * wants to die" and "she told me she has a plan" do not, and neither does "my
   * friend wants to die" (relations are blanked by `hasFirstPerson`).
   */
  private hitIsAboutTheSpeaker(hit: Occurrence): boolean {
    const clause = hit.clause;
    const offset = Math.max(0, hit.start - clause.start);
    const before = pySplit(this.patterns.tables, clause.text.slice(0, offset)).slice(-1);
    const subjects = new Set(this.patterns.engine.first_person_subjects);
    if (before.length > 0 && subjects.has(before[0] as string)) {
      return true;
    }
    const span = clause.text.slice(offset, hit.end - clause.start);
    return this.normaliser.hasFirstPerson(span);
  }

  /** Every pragmatic judgement about this message, made exactly once. */
  private buildFlags(
    text: string,
    message: NormalisedMessage,
    occurrences: Occurrence[],
    accepted: Occurrence[],
  ): MessageFlags {
    const context = this.patterns.context;
    const fictionFrame = context.fictionFramesPattern.test(message.primary);
    let quotedOnly = this.isQuotedOnly(text, message, occurrences);
    if (quotedOnly) {
      // "my diary says '…'" quotes the speaker's *own* words. Reading them as
      // somebody else's gave a person in crisis the supporter template and capped
      // an imminent plan at HIGH (Day 29, RT-S-QT-01..03).
      const [outside] = this.normaliser.splitQuotes(text);
      if (this.selfQuote.test(this.normaliser.toPrimary(outside))) {
        quotedOnly = false;
      }
    }
    return {
      first_person: this.normaliser.hasFirstPerson(message.primary),
      third_person: quotedOnly || fictionFrame || this.isThirdPerson(message, accepted),
      quoted_only: quotedOnly,
      fiction_frame: fictionFrame,
      timeframe: context.timeframePattern.test(message.primary),
    };
  }

  /** Turn accepted hits into a level, before the third-person cap. */
  private levelFor(options: {
    accepted: Occurrence[];
    categories: RiskCategory[];
    negatedCount: number;
    thirdPerson: boolean;
    timeframe: boolean;
  }): RiskLevel {
    const { accepted, categories, negatedCount, thirdPerson, timeframe } = options;
    if (accepted.length === 0) {
      // Negated ideation is not nothing: it is a person telling a mental-health
      // companion about death. LOW earns a warm check-in, not a crisis card.
      return negatedCount > 0 ? "low" : "none";
    }

    // The pattern data carries the level, including the rules that say an overdose
    // has already been taken. The engine does not second-guess them — it only raises
    // the ceiling when several signals line up.
    const levels = this.patterns.engine.levels;
    let level: RiskLevel = "low";
    for (const hit of accepted) {
      if (levels[hit.rule.data.level] > levels[level]) {
        level = hit.rule.data.level;
      }
    }

    // A plan plus a time, or a plan plus access, is what separates "i have been
    // thinking about this" from "this is happening".
    if (
      categories.includes("intent_plan") &&
      levels[level] >= levels.high &&
      !thirdPerson &&
      (timeframe || categories.includes("access_to_means"))
    ) {
      return "imminent";
    }
    return level;
  }

  /**
   * Cap a message about somebody else at HIGH — except a medical emergency.
   *
   * "my friend says he has a plan for tonight" is a crisis, but it is not the
   * *speaker's* imminent attempt, and the reply that helps is "get them to a person,
   * or call emergency services". An overdose that has already happened keeps
   * IMMINENT: an ambulance is needed regardless of who the sentence is about.
   */
  private capForThirdPerson(
    level: RiskLevel,
    categories: RiskCategory[],
    thirdPerson: boolean,
  ): RiskLevel {
    if (!thirdPerson || level !== "imminent") {
      return level;
    }
    return categories.includes("acute_medical") ? "imminent" : "high";
  }
}

let cached: RuleEngine | null = null;
let cachedFor: CompiledPatternSet | null = null;

/**
 * The shared engine for one compiled pattern set (`rules.build_engine`).
 *
 * Cached per pattern set, so the app builds the matchers once and every message
 * reuses them; a test that compiles a different bundle gets its own engine.
 */
export function buildRuleEngine(
  patterns: CompiledPatternSet,
  options: RuleEngineOptions = {},
): RuleEngine {
  if (options.maxChars !== undefined) {
    return new RuleEngine(patterns, options);
  }
  if (cached === null || cachedFor !== patterns) {
    cached = new RuleEngine(patterns);
    cachedFor = patterns;
  }
  return cached;
}
