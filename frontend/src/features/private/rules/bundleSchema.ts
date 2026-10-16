/**
 * The shape of `public/private/safety-bundle.json`, and the loader that refuses
 * a bundle it does not recognise.
 *
 * The bundle is compiled at build time from `backend/app/content/**` by
 * `frontend/scripts/build-private-bundle.mjs`; the Python side of the contract is
 * `backend/tests/safety/test_private_bundle.py`. Nothing here invents content: a
 * missing field, an unknown level or a rule that does not compile is a hard
 * error, exactly as `patterns.py` fails the API at startup. A Private Mode that
 * quietly ran with half a pattern set would be worse than one that refuses to
 * start.
 *
 * Types first, then the narrowing helpers, then the parse — in that order so the
 * reader meets the contract before the enforcement.
 */

import {
  compileMarkerPattern,
  compilePhrase,
  compileRelationPattern,
  compileTranslated,
  escapeLiteral,
} from "./pyRegex";
import { wordClassBody } from "./pyStrings";
import type { CompiledMatcher } from "./pyRegex";

export const BUNDLE_FORMAT = "manovia-private-bundle/1";

/** `base.RiskLevel.label` — the wire form both engines speak. */
export type RiskLevel = "none" | "low" | "medium" | "high" | "imminent";

/** `base.RiskCategory` — the eight things the engine looks for. */
export type RiskCategory =
  | "suicidal_ideation"
  | "self_harm"
  | "intent_plan"
  | "access_to_means"
  | "harm_to_others"
  | "abuse_disclosure"
  | "severe_hopelessness"
  | "acute_medical";

export type RuleKind = "phrase" | "regex";
export type RuleSpelling = "fused" | "obfuscated" | null;
export type RuleScript = "devanagari" | "bengali" | "latin" | "arabic" | null;

export interface UnicodeTables {
  /** Python's `\s`, which JavaScript's `\s` is not. */
  whitespace_codepoints: number[];
  /** Python's `\w`, as a JavaScript class body. */
  word_class_body: string;
  /** Python's `\d`. */
  digit_class: string;
  invisible_codepoints: number[];
  apostrophe_codepoints: number[];
  kept_mark_ranges: [number, number][];
  latin_combining_ranges: [number, number][];
  /** Cyrillic and Greek look-alikes, keyed by code point. */
  confusables: Record<string, string>;
  /** `str.casefold()` where `toLowerCase()` disagrees. */
  casefold_overrides: Record<string, string>;
  casefold_fixture: { count: number; unicodedata_version: string };
}

export interface RuleData {
  id: string;
  category: RiskCategory;
  level: Exclude<RiskLevel, "none">;
  kind: RuleKind;
  /** Verbatim from the YAML: normalised-form text or a Python regular expression. */
  value: string;
  negation_sensitive: boolean;
  negation_baked: boolean;
  figurative_prone: boolean;
  figurative_subject: boolean;
  language: string | null;
  script: RuleScript;
  spelling: RuleSpelling;
}

export interface ContextVocabulary {
  leet_map: Record<string, string>;
  /** `[source, replacement]`, already in Python's longest-source-first order. */
  misspellings: [string, string][];
  negation_cues: string[];
  negation_after_cues: string[];
  transparent_words: string[];
  third_person_markers: string[];
  third_person_patterns: string[];
  fiction_frames: string[];
  relation_possessives: string[];
  relation_nouns: string[];
  first_person_markers: string[];
  timeframe_markers: string[];
  benign_frames: string[];
  benign_subjects: string[];
}

/**
 * The rationale codes the engine may report — a closed set, keyed by role.
 *
 * Typed as a record with named keys rather than `Record<string, string>` so a
 * misspelled code is a compile error, and so `noUncheckedIndexedAccess` cannot
 * turn every lookup into `string | undefined`.
 */
export interface RationaleCodes {
  negated: string;
  figurative: string;
  third_person: string;
  quoted: string;
  fiction_frame: string;
  timeframe: string;
  empty_input: string;
  truncated: string;
  windowed: string;
}

const CODE_KEYS: readonly (keyof RationaleCodes)[] = [
  "negated",
  "figurative",
  "third_person",
  "quoted",
  "fiction_frame",
  "timeframe",
  "empty_input",
  "truncated",
  "windowed",
];

function parseCodes(value: unknown): RationaleCodes {
  const raw = asRecord(value, "engine.codes");
  const unknown = Object.keys(raw).filter((key) => !(CODE_KEYS as readonly string[]).includes(key));
  if (unknown.length > 0) {
    throw new BundleError(`engine.codes has unknown keys: ${unknown.join(", ")}`);
  }
  const codes = {} as Record<string, string>;
  for (const key of CODE_KEYS) {
    codes[key] = asString(raw[key], `engine.codes.${key}`);
  }
  return codes as unknown as RationaleCodes;
}

/** The four stored tiers (`app.models.enums.RiskLevel`). */
export type StoredLevel = "none" | "caution" | "elevated" | "crisis";

const STORED_LEVEL_NAMES: readonly StoredLevel[] = ["none", "caution", "elevated", "crisis"];

function parsePrompts(value: unknown): Record<string, PromptSource> {
  const raw = asRecord(value, "prompts");
  const out: Record<string, PromptSource> = {};
  for (const [key, entry] of Object.entries(raw)) {
    const record = asRecord(entry, `prompts.${key}`);
    const text = asString(record.text, `prompts.${key}.text`);
    const words = text.split(/\s+/).filter(Boolean).length;
    if (record.words !== words) {
      throw new BundleError(
        `prompts.${key}.words is ${String(record.words)} but the text has ${words}`,
      );
    }
    out[key] = {
      path: asString(record.path, `prompts.${key}.path`),
      sha256: asString(record.sha256, `prompts.${key}.sha256`),
      bytes: typeof record.bytes === "number" ? record.bytes : 0,
      words,
      text,
    };
  }
  if (Object.keys(out).length === 0) {
    throw new BundleError("prompts is empty");
  }
  return out;
}

function parseStoredLevels(value: unknown): Record<RiskLevel, StoredLevel> {
  const raw = asRecord(value, "engine.stored_levels");
  const out = {} as Record<string, StoredLevel>;
  for (const level of RISK_LEVELS) {
    const stored = asString(raw[level], `engine.stored_levels.${level}`);
    if (!(STORED_LEVEL_NAMES as readonly string[]).includes(stored)) {
      throw new BundleError(`engine.stored_levels.${level} is not a stored tier`);
    }
    out[level] = stored as StoredLevel;
  }
  return out as Record<RiskLevel, StoredLevel>;
}

/** One reviewed system prompt, shipped so the guard can check for prompt echoes. */
export interface PromptSource {
  /** Path relative to `backend/app/`, matching `bundle.sources`. */
  path: string;
  sha256: string;
  bytes: number;
  words: number;
  text: string;
}

export interface EngineData {
  max_input_chars: number;
  max_backward_tokens: number;
  max_transparent_skips: number;
  max_forward_tokens: number;
  subject_window: number;
  window_overlap_chars: number;
  max_windowed_chars: number;
  clause_bar: string;
  levels: Record<RiskLevel, number>;
  /**
   * `RiskLevel.to_stored()`: the four-tier database label for each level
   * (`none`, `caution`, `elevated`, `crisis`). LOW is stored as CAUTION and MEDIUM
   * as ELEVATED, so this is data, not something to guess from the label.
   */
  stored_levels: Record<RiskLevel, StoredLevel>;
  categories: RiskCategory[];
  codes: RationaleCodes;
  first_person_subjects: string[];
  self_quote_pattern: string;
  rules: RuleData[];
  context: ContextVocabulary;
}

export interface OutputGuardRule {
  id: string;
  category: string;
  kind: RuleKind;
  value: string;
  negation_sensitive: boolean;
  script: string | null;
}

export interface OutputGuardData {
  rules: OutputGuardRule[];
  safe_replies: Record<string, string>;
  trimmed_closings: Record<string, string>;
  check_in_phrases: string[];
  negation_cues: string[];
}

/** `crisis.CrisisTemplate`, in the shape `content/i18n/*.json` stores it. */
export interface CrisisTemplate {
  title: string;
  body: string[];
  helpline_intro: string | null;
  emergency_instruction: string | null;
  trusted_person: string | null;
  safety_steps: string[];
  closing: string | null;
  disclaimer: string | null;
}

export interface LocalisedCrisisContent {
  locale: string;
  placeholder_whitelist: string[];
  templates: Record<string, CrisisTemplate>;
}

/** `escalation.EscalationPolicy` for one level. */
export interface EscalationPolicy {
  level: RiskLevel;
  template_id: string | null;
  about_others_template_id: string | null;
  actions: string[];
  /** False at HIGH and IMMINENT: no model call happens for this turn. */
  allow_llm: boolean;
  deterministic_reply: boolean;
  show_helplines: boolean;
  show_emergency_instruction: boolean;
  record_event: boolean;
}

/** `crisis.CrisisResource`, with the contact hrefs already computed. */
export interface HelplineResource {
  id: string;
  region: string;
  kind: "emergency" | "crisis_line" | "text_line" | "directory";
  name: string;
  number: string | null;
  type: "call" | "text" | "chat" | "web";
  text_keyword: string | null;
  instructions: string | null;
  hours: string;
  languages: string[];
  audience: string | null;
  description: string;
  url: string | null;
  source_url: string;
  last_verified: string;
  needs_verification: boolean;
  verification_note: string | null;
  priority: number;
  tel_href: string | null;
  sms_href: string | null;
}

export interface CrisisData {
  international_emergency_number: string;
  fallback_region: string;
  fallback_locale: string;
  policy: Record<RiskLevel, EscalationPolicy>;
  locales: Record<string, LocalisedCrisisContent>;
  helplines: {
    version: number;
    last_verified: string;
    source: string;
    disclaimer: string;
    regions: string[];
    resources: HelplineResource[];
  };
}

export interface PrivateBundle {
  format: string;
  generated_by: string;
  sources: Record<string, { sha256: string; bytes: number }>;
  duplicates_tolerated: { file: string; message: string }[];
  unicode: UnicodeTables;
  engine: EngineData;
  /**
   * The system prompts, keyed by file stem (`system_v1`, ...). The private session
   * gives the shortest reviewed one to the on-device model, and the output guard
   * builds its echo shingles from all of them — the same files Python reads.
   */
  prompts: Record<string, PromptSource>;
  output_guard: OutputGuardData;
  crisis: CrisisData;
}

/** Raised for a bundle that is not the bundle this code was written against. */
export class BundleError extends Error {
  constructor(message: string) {
    super(`private bundle: ${message}`);
    this.name = "BundleError";
  }
}

// --------------------------------------------------------------------------- //
// Narrowing helpers — `unknown` in, typed out, with the field path in the error
// --------------------------------------------------------------------------- //

function asRecord(value: unknown, path: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new BundleError(`${path} must be an object`);
  }
  return value as Record<string, unknown>;
}

function asString(value: unknown, path: string): string {
  if (typeof value !== "string") {
    throw new BundleError(`${path} must be a string`);
  }
  return value;
}

function asNullableString(value: unknown, path: string): string | null {
  if (value === null) {
    return null;
  }
  return asString(value, path);
}

function asBoolean(value: unknown, path: string): boolean {
  if (typeof value !== "boolean") {
    throw new BundleError(`${path} must be a boolean`);
  }
  return value;
}

function asNumber(value: unknown, path: string): number {
  if (typeof value !== "number" || !Number.isInteger(value)) {
    throw new BundleError(`${path} must be an integer`);
  }
  return value;
}

function asStringArray(value: unknown, path: string): string[] {
  if (!Array.isArray(value) || value.some((item) => typeof item !== "string")) {
    throw new BundleError(`${path} must be an array of strings`);
  }
  return [...(value as string[])];
}

function asNumberArray(value: unknown, path: string): number[] {
  if (!Array.isArray(value) || value.some((item) => typeof item !== "number")) {
    throw new BundleError(`${path} must be an array of numbers`);
  }
  return [...(value as number[])];
}

function asRanges(value: unknown, path: string): [number, number][] {
  if (
    !Array.isArray(value) ||
    value.some(
      (item) =>
        !Array.isArray(item) ||
        item.length !== 2 ||
        item.some((bound) => typeof bound !== "number"),
    )
  ) {
    throw new BundleError(`${path} must be an array of [low, high] pairs`);
  }
  return (value as number[][]).map(([low, high]) => [low as number, high as number]);
}

function asStringRecord(value: unknown, path: string): Record<string, string> {
  const record = asRecord(value, path);
  const out: Record<string, string> = {};
  for (const [key, item] of Object.entries(record)) {
    out[key] = asString(item, `${path}.${key}`);
  }
  return out;
}

const RISK_LEVELS: readonly RiskLevel[] = ["none", "low", "medium", "high", "imminent"];

function parseCategories(value: unknown): RiskCategory[] {
  const declared = asStringArray(value, "engine.categories");
  if (declared.join(",") !== CATEGORIES.join(",")) {
    throw new BundleError(
      `engine.categories must be the eight risk categories in order, got ${declared.join(",")}`,
    );
  }
  return [...CATEGORIES];
}
const PATTERN_LEVELS: readonly Exclude<RiskLevel, "none">[] = ["low", "medium", "high", "imminent"];
const CATEGORIES: readonly RiskCategory[] = [
  "suicidal_ideation",
  "self_harm",
  "intent_plan",
  "access_to_means",
  "harm_to_others",
  "abuse_disclosure",
  "severe_hopelessness",
  "acute_medical",
];

export function isRiskLevel(value: string): value is RiskLevel {
  return (RISK_LEVELS as readonly string[]).includes(value);
}

export function levelRank(levels: Record<RiskLevel, number>, level: RiskLevel): number {
  return levels[level];
}

function parseRule(value: unknown, path: string): RuleData {
  const raw = asRecord(value, path);
  const level = asString(raw.level, `${path}.level`);
  if (!(PATTERN_LEVELS as readonly string[]).includes(level)) {
    throw new BundleError(`${path}.level must be one of ${PATTERN_LEVELS.join(", ")}`);
  }
  const category = asString(raw.category, `${path}.category`);
  if (!(CATEGORIES as readonly string[]).includes(category)) {
    throw new BundleError(`${path}.category is not one of the eight risk categories`);
  }
  const kind = asString(raw.kind, `${path}.kind`);
  if (kind !== "phrase" && kind !== "regex") {
    throw new BundleError(`${path}.kind must be phrase or regex`);
  }
  return {
    id: asString(raw.id, `${path}.id`),
    category: category as RiskCategory,
    level: level as Exclude<RiskLevel, "none">,
    kind,
    value: asString(raw.value, `${path}.value`),
    negation_sensitive: asBoolean(raw.negation_sensitive, `${path}.negation_sensitive`),
    negation_baked: asBoolean(raw.negation_baked, `${path}.negation_baked`),
    figurative_prone: asBoolean(raw.figurative_prone, `${path}.figurative_prone`),
    figurative_subject: asBoolean(raw.figurative_subject, `${path}.figurative_subject`),
    language: asNullableString(raw.language, `${path}.language`),
    script: (raw.script ?? null) as RuleScript,
    spelling: (raw.spelling ?? null) as RuleSpelling,
  };
}

function parseMisspellings(value: unknown, path: string): [string, string][] {
  if (
    !Array.isArray(value) ||
    value.some(
      (item) =>
        !Array.isArray(item) || item.length !== 2 || item.some((p) => typeof p !== "string"),
    )
  ) {
    throw new BundleError(`${path} must be an array of [source, replacement] pairs`);
  }
  return (value as string[][]).map(([source, target]) => [source as string, target as string]);
}

/** Validate a parsed bundle document. `JSON.parse` output in, typed bundle out. */
export function parseBundle(document: unknown): PrivateBundle {
  const root = asRecord(document, "bundle");
  const format = asString(root.format, "format");
  if (format !== BUNDLE_FORMAT) {
    throw new BundleError(
      `format ${JSON.stringify(format)} is not ${JSON.stringify(BUNDLE_FORMAT)} — rebuild it`,
    );
  }

  const unicodeRaw = asRecord(root.unicode, "unicode");
  const unicode: UnicodeTables = {
    whitespace_codepoints: asNumberArray(
      unicodeRaw.whitespace_codepoints,
      "unicode.whitespace_codepoints",
    ),
    word_class_body: asString(unicodeRaw.word_class_body, "unicode.word_class_body"),
    digit_class: asString(unicodeRaw.digit_class, "unicode.digit_class"),
    invisible_codepoints: asNumberArray(
      unicodeRaw.invisible_codepoints,
      "unicode.invisible_codepoints",
    ),
    apostrophe_codepoints: asNumberArray(
      unicodeRaw.apostrophe_codepoints,
      "unicode.apostrophe_codepoints",
    ),
    kept_mark_ranges: asRanges(unicodeRaw.kept_mark_ranges, "unicode.kept_mark_ranges"),
    latin_combining_ranges: asRanges(
      unicodeRaw.latin_combining_ranges,
      "unicode.latin_combining_ranges",
    ),
    confusables: asStringRecord(unicodeRaw.confusables, "unicode.confusables"),
    casefold_overrides: asStringRecord(unicodeRaw.casefold_overrides, "unicode.casefold_overrides"),
    casefold_fixture: {
      count: asNumber(
        asRecord(unicodeRaw.casefold_fixture, "unicode.casefold_fixture").count,
        "unicode.casefold_fixture.count",
      ),
      unicodedata_version: asString(
        asRecord(unicodeRaw.casefold_fixture, "unicode.casefold_fixture").unicodedata_version,
        "unicode.casefold_fixture.unicodedata_version",
      ),
    },
  };
  if (unicode.whitespace_codepoints.length === 0) {
    throw new BundleError("unicode.whitespace_codepoints is empty");
  }

  const engineRaw = asRecord(root.engine, "engine");
  const contextRaw = asRecord(engineRaw.context, "engine.context");
  const context: ContextVocabulary = {
    leet_map: asStringRecord(contextRaw.leet_map, "engine.context.leet_map"),
    misspellings: parseMisspellings(contextRaw.misspellings, "engine.context.misspellings"),
    negation_cues: asStringArray(contextRaw.negation_cues, "engine.context.negation_cues"),
    negation_after_cues: asStringArray(
      contextRaw.negation_after_cues,
      "engine.context.negation_after_cues",
    ),
    transparent_words: asStringArray(
      contextRaw.transparent_words,
      "engine.context.transparent_words",
    ),
    third_person_markers: asStringArray(
      contextRaw.third_person_markers,
      "engine.context.third_person_markers",
    ),
    third_person_patterns: asStringArray(
      contextRaw.third_person_patterns,
      "engine.context.third_person_patterns",
    ),
    fiction_frames: asStringArray(contextRaw.fiction_frames, "engine.context.fiction_frames"),
    relation_possessives: asStringArray(
      contextRaw.relation_possessives,
      "engine.context.relation_possessives",
    ),
    relation_nouns: asStringArray(contextRaw.relation_nouns, "engine.context.relation_nouns"),
    first_person_markers: asStringArray(
      contextRaw.first_person_markers,
      "engine.context.first_person_markers",
    ),
    timeframe_markers: asStringArray(
      contextRaw.timeframe_markers,
      "engine.context.timeframe_markers",
    ),
    benign_frames: asStringArray(contextRaw.benign_frames, "engine.context.benign_frames"),
    benign_subjects: asStringArray(contextRaw.benign_subjects, "engine.context.benign_subjects"),
  };

  const levelsRaw = asRecord(engineRaw.levels, "engine.levels");
  const levels = Object.fromEntries(
    RISK_LEVELS.map((level) => [level, asNumber(levelsRaw[level], `engine.levels.${level}`)]),
  ) as Record<RiskLevel, number>;

  const rulesRaw = engineRaw.rules;
  if (!Array.isArray(rulesRaw) || rulesRaw.length === 0) {
    throw new BundleError("engine.rules must be a non-empty array — refusing a blind engine");
  }
  const rules = rulesRaw.map((entry, index) => parseRule(entry, `engine.rules[${index}]`));
  const ids = new Set<string>();
  for (const rule of rules) {
    if (ids.has(rule.id)) {
      throw new BundleError(`duplicate rule id ${rule.id}`);
    }
    ids.add(rule.id);
  }

  const codesRaw = asRecord(engineRaw.codes, "engine.codes");
  const engine: EngineData = {
    max_input_chars: asNumber(engineRaw.max_input_chars, "engine.max_input_chars"),
    max_backward_tokens: asNumber(engineRaw.max_backward_tokens, "engine.max_backward_tokens"),
    max_transparent_skips: asNumber(
      engineRaw.max_transparent_skips,
      "engine.max_transparent_skips",
    ),
    max_forward_tokens: asNumber(engineRaw.max_forward_tokens, "engine.max_forward_tokens"),
    subject_window: asNumber(engineRaw.subject_window, "engine.subject_window"),
    window_overlap_chars: asNumber(engineRaw.window_overlap_chars, "engine.window_overlap_chars"),
    max_windowed_chars: asNumber(engineRaw.max_windowed_chars, "engine.max_windowed_chars"),
    clause_bar: asString(engineRaw.clause_bar, "engine.clause_bar"),
    levels,
    stored_levels: parseStoredLevels(engineRaw.stored_levels),
    categories: parseCategories(engineRaw.categories),
    codes: parseCodes(codesRaw),
    first_person_subjects: asStringArray(
      engineRaw.first_person_subjects,
      "engine.first_person_subjects",
    ),
    self_quote_pattern: asString(engineRaw.self_quote_pattern, "engine.self_quote_pattern"),
    rules,
    context,
  };

  const guardRaw = asRecord(root.output_guard, "output_guard");
  const guardRulesRaw = guardRaw.rules;
  if (!Array.isArray(guardRulesRaw)) {
    throw new BundleError("output_guard.rules must be an array");
  }
  const outputGuard: OutputGuardData = {
    rules: guardRulesRaw.map((entry, index) => {
      const raw = asRecord(entry, `output_guard.rules[${index}]`);
      const kind = asString(raw.kind, `output_guard.rules[${index}].kind`);
      if (kind !== "phrase" && kind !== "regex") {
        throw new BundleError(`output_guard.rules[${index}].kind must be phrase or regex`);
      }
      return {
        id: asString(raw.id, `output_guard.rules[${index}].id`),
        category: asString(raw.category, `output_guard.rules[${index}].category`),
        kind,
        value: asString(raw.value, `output_guard.rules[${index}].value`),
        negation_sensitive: asBoolean(
          raw.negation_sensitive,
          `output_guard.rules[${index}].negation_sensitive`,
        ),
        script: asNullableString(raw.script, `output_guard.rules[${index}].script`),
      };
    }),
    safe_replies: asStringRecord(guardRaw.safe_replies, "output_guard.safe_replies"),
    trimmed_closings: asStringRecord(guardRaw.trimmed_closings, "output_guard.trimmed_closings"),
    check_in_phrases: asStringArray(guardRaw.check_in_phrases, "output_guard.check_in_phrases"),
    negation_cues: asStringArray(guardRaw.negation_cues, "output_guard.negation_cues"),
  };

  const crisisRaw = asRecord(root.crisis, "crisis");
  const policyRaw = asRecord(crisisRaw.policy, "crisis.policy");
  const policy = Object.fromEntries(
    RISK_LEVELS.map((level) => {
      const raw = asRecord(policyRaw[level], `crisis.policy.${level}`);
      const entry: EscalationPolicy = {
        level,
        template_id: asNullableString(raw.template_id, `crisis.policy.${level}.template_id`),
        about_others_template_id: asNullableString(
          raw.about_others_template_id,
          `crisis.policy.${level}.about_others_template_id`,
        ),
        actions: asStringArray(raw.actions, `crisis.policy.${level}.actions`),
        allow_llm: asBoolean(raw.allow_llm, `crisis.policy.${level}.allow_llm`),
        deterministic_reply: asBoolean(
          raw.deterministic_reply,
          `crisis.policy.${level}.deterministic_reply`,
        ),
        show_helplines: asBoolean(raw.show_helplines, `crisis.policy.${level}.show_helplines`),
        show_emergency_instruction: asBoolean(
          raw.show_emergency_instruction,
          `crisis.policy.${level}.show_emergency_instruction`,
        ),
        record_event: asBoolean(raw.record_event, `crisis.policy.${level}.record_event`),
      };
      return [level, entry];
    }),
  ) as Record<RiskLevel, EscalationPolicy>;

  const helplinesRaw = asRecord(crisisRaw.helplines, "crisis.helplines");
  const resourcesRaw = helplinesRaw.resources;
  if (!Array.isArray(resourcesRaw) || resourcesRaw.length === 0) {
    throw new BundleError("crisis.helplines.resources must be a non-empty array");
  }
  const resources = resourcesRaw.map((entry, index) => {
    const raw = asRecord(entry, `crisis.helplines.resources[${index}]`);
    const at = (field: string): string => `crisis.helplines.resources[${index}].${field}`;
    return {
      id: asString(raw.id, at("id")),
      region: asString(raw.region, at("region")),
      kind: raw.kind as HelplineResource["kind"],
      name: asString(raw.name, at("name")),
      number: asNullableString(raw.number, at("number")),
      type: raw.type as HelplineResource["type"],
      text_keyword: asNullableString(raw.text_keyword, at("text_keyword")),
      instructions: asNullableString(raw.instructions, at("instructions")),
      hours: asString(raw.hours, at("hours")),
      languages: asStringArray(raw.languages, at("languages")),
      audience: asNullableString(raw.audience, at("audience")),
      description: asString(raw.description, at("description")),
      url: asNullableString(raw.url, at("url")),
      source_url: asString(raw.source_url, at("source_url")),
      last_verified: asString(raw.last_verified, at("last_verified")),
      needs_verification: asBoolean(raw.needs_verification, at("needs_verification")),
      verification_note: asNullableString(raw.verification_note, at("verification_note")),
      priority: asNumber(raw.priority, at("priority")),
      tel_href: asNullableString(raw.tel_href, at("tel_href")),
      sms_href: asNullableString(raw.sms_href, at("sms_href")),
    };
  });

  const localesRaw = asRecord(crisisRaw.locales, "crisis.locales");
  const locales: Record<string, LocalisedCrisisContent> = {};
  for (const [locale, value] of Object.entries(localesRaw)) {
    const raw = asRecord(value, `crisis.locales.${locale}`);
    const templatesRaw = asRecord(raw.templates, `crisis.locales.${locale}.templates`);
    const templates: Record<string, CrisisTemplate> = {};
    for (const [templateId, templateValue] of Object.entries(templatesRaw)) {
      const template = asRecord(templateValue, `crisis.locales.${locale}.templates.${templateId}`);
      templates[templateId] = {
        title: asString(template.title, "title"),
        body: asStringArray(template.body, "body"),
        helpline_intro: asNullableString(template.helpline_intro, "helpline_intro"),
        emergency_instruction: asNullableString(
          template.emergency_instruction,
          "emergency_instruction",
        ),
        trusted_person: asNullableString(template.trusted_person, "trusted_person"),
        safety_steps: asStringArray(template.safety_steps, "safety_steps"),
        closing: asNullableString(template.closing, "closing"),
        disclaimer: asNullableString(template.disclaimer, "disclaimer"),
      };
    }
    locales[locale] = {
      locale: asString(raw.locale, `crisis.locales.${locale}.locale`),
      placeholder_whitelist: asStringArray(
        raw.placeholder_whitelist,
        `crisis.locales.${locale}.placeholder_whitelist`,
      ),
      templates,
    };
  }

  const crisis: CrisisData = {
    international_emergency_number: asString(
      crisisRaw.international_emergency_number,
      "crisis.international_emergency_number",
    ),
    fallback_region: asString(crisisRaw.fallback_region, "crisis.fallback_region"),
    fallback_locale: asString(crisisRaw.fallback_locale, "crisis.fallback_locale"),
    policy,
    locales,
    helplines: {
      version: asNumber(helplinesRaw.version, "crisis.helplines.version"),
      last_verified: asString(helplinesRaw.last_verified, "crisis.helplines.last_verified"),
      source: asString(helplinesRaw.source, "crisis.helplines.source"),
      disclaimer: asString(helplinesRaw.disclaimer, "crisis.helplines.disclaimer"),
      regions: asStringArray(helplinesRaw.regions, "crisis.helplines.regions"),
      resources,
    },
  };

  const sourcesRaw = asRecord(root.sources, "sources");
  const sources: Record<string, { sha256: string; bytes: number }> = {};
  for (const [name, value] of Object.entries(sourcesRaw)) {
    const raw = asRecord(value, `sources.${name}`);
    sources[name] = {
      sha256: asString(raw.sha256, `sources.${name}.sha256`),
      bytes: asNumber(raw.bytes, `sources.${name}.bytes`),
    };
  }
  const duplicatesRaw = root.duplicates_tolerated;
  const duplicates = Array.isArray(duplicatesRaw)
    ? duplicatesRaw.map((entry, index) => {
        const raw = asRecord(entry, `duplicates_tolerated[${index}]`);
        return {
          file: asString(raw.file, `duplicates_tolerated[${index}].file`),
          message: asString(raw.message, `duplicates_tolerated[${index}].message`),
        };
      })
    : [];

  return {
    format,
    generated_by: asString(root.generated_by, "generated_by"),
    sources,
    duplicates_tolerated: duplicates,
    unicode,
    engine,
    prompts: parsePrompts(root.prompts),
    output_guard: outputGuard,
    crisis,
  };
}

// --------------------------------------------------------------------------- //
// Compilation
// --------------------------------------------------------------------------- //

/** One detection rule with its three projections compiled. */
export interface CompiledRule {
  data: RuleData;
  pattern: CompiledMatcher;
  squashed: CompiledMatcher | null;
  collapsed: CompiledMatcher | null;
}

/** `patterns.ContextData`: the vocabulary, compiled. */
export interface CompiledContext {
  vocabulary: ContextVocabulary;
  leetMap: Map<string, string>;
  misspellings: [RegExp, string][];
  negationCues: Set<string>;
  negationAfterCues: Set<string>;
  transparentWords: Set<string>;
  thirdPersonPatterns: CompiledMatcher[];
  fictionFramesPattern: CompiledMatcher;
  firstPersonPattern: CompiledMatcher;
  relationPattern: CompiledMatcher;
  timeframePattern: CompiledMatcher;
  benignFrames: CompiledMatcher[];
  benignSubjectPattern: CompiledMatcher;
  thirdPersonMarkerPattern: CompiledMatcher;
}

/** `patterns.PatternSet`: everything the engine needs, compiled once. */
export interface CompiledPatternSet {
  tables: UnicodeTables;
  engine: EngineData;
  rules: CompiledRule[];
  context: CompiledContext;
  selfNegating: ReadonlySet<string>;
}

/**
 * Compile a parsed bundle into the matchers the engine runs.
 *
 * Done once per bundle (the caller caches it), because compiling 215 rules with
 * lookbehind-expanded boundaries is the expensive part; matching is cheap.
 */
export function compilePatternSet(bundle: PrivateBundle): CompiledPatternSet {
  const tables = bundle.unicode;
  const rules = bundle.engine.rules.map((data) => {
    if (data.kind === "phrase") {
      const compiled = compilePhrase(data.value, tables);
      return { data, ...compiled };
    }
    return {
      data,
      pattern: compileTranslated(data.value, tables),
      squashed: null,
      collapsed: null,
    };
  });

  const vocabulary = bundle.engine.context;
  const word = `[${wordClassBody(tables)}]`;
  const context: CompiledContext = {
    vocabulary,
    leetMap: new Map(Object.entries(vocabulary.leet_map)),
    // The compiler already sorted these longest-source-first (Python's order);
    // the browser only has to wrap them as word-bounded patterns, exactly as
    // `patterns._compile_context` does. The replacement is a function so a `$`
    // in a target word can never be read as a group reference.
    misspellings: vocabulary.misspellings.map(([source, target]) => [
      new RegExp(`(?<!${word})${escapeLiteral(source)}(?!${word})`, "gu"),
      target,
    ]),
    negationCues: new Set(vocabulary.negation_cues),
    negationAfterCues: new Set(vocabulary.negation_after_cues),
    transparentWords: new Set(vocabulary.transparent_words),
    thirdPersonPatterns: vocabulary.third_person_patterns.map((source) =>
      compileTranslated(source, tables),
    ),
    fictionFramesPattern: compileMarkerPattern(vocabulary.fiction_frames, tables),
    firstPersonPattern: compileMarkerPattern(vocabulary.first_person_markers, tables),
    relationPattern: compileRelationPattern(
      vocabulary.relation_possessives,
      vocabulary.relation_nouns,
      tables,
    ),
    timeframePattern: compileMarkerPattern(vocabulary.timeframe_markers, tables),
    benignFrames: vocabulary.benign_frames.map((source) => compileTranslated(source, tables)),
    benignSubjectPattern: compileMarkerPattern(vocabulary.benign_subjects, tables),
    thirdPersonMarkerPattern: compileMarkerPattern(vocabulary.third_person_markers, tables),
  };

  return {
    tables,
    engine: bundle.engine,
    rules,
    context,
    selfNegating: new Set(
      rules.filter((rule) => rule.data.negation_baked).map((rule) => rule.data.id),
    ),
  };
}
