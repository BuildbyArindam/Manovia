#!/usr/bin/env node
/**
 * Day 41: compile the safety content into one JSON bundle for the browser.
 *
 * Private Mode runs the rules engine, the output guard and the deterministic
 * crisis copy on the device, with no server in the loop. It cannot read
 * `backend/app/content/**` at runtime, so this script compiles that content —
 * the same files the Python engine loads — into a single artifact the browser
 * can fetch once and cache:
 *
 *     frontend/public/private/safety-bundle.json
 *
 * WHAT GOES IN
 *
 * 1. `engine`     — every detection rule from `content/safety/patterns_*.yaml`
 *                   (values verbatim, as Python compiles them), the pragmatics
 *                   vocabulary from `patterns_context.yaml`, and the engine's
 *                   numeric constants and Unicode tables. Pattern *sources*
 *                   stay in Python regular-expression syntax: the browser
 *                   translator lives in `src/features/private/rules/regex.ts`
 *                   next to the tests that hold it to Python's semantics.
 * 2. `output_guard` — `content/safety/output_patterns.yaml`: the reply-side
 *                   rules plus the pre-written safe replies.
 * 3. `crisis`     — the escalation policy table, the localised crisis and
 *                   check-in templates, and `helplines.json` with `tel_href` /
 *                   `sms_href` already computed.
 *
 * WHY A BUNDLE AND NOT A SECOND COPY OF THE DATA
 *
 * AGENTS.md safety rule 6 says helpline data lives only in
 * `backend/app/content/helplines.json`; the same argument holds for the safety
 * vocabulary. This file is *derived*: it is regenerated on every `npm run build`,
 * and two tests make drift impossible rather than merely unlikely —
 * `backend/tests/safety/test_private_bundle.py` compares the bundle against
 * what Python actually compiles (rules, vocabulary, constants, policy, unicode
 * tables), and `src/features/private/bundle/drift.test.ts` recompiles here and
 * fails if the committed artifact differs. Editing a YAML without rebuilding
 * therefore fails both suites.
 *
 * The bundle is served as a static asset, not imported into a JS chunk, so it
 * never lands in the initial bundle, a route increment or the service worker
 * precache (`scripts/check-bundle-budget.mjs` budgets all three). Private Mode
 * downloads it through the same consent-and-integrity path as the models and
 * keeps it in the Cache API, which is what makes an offline reload work.
 *
 * USAGE
 *   node scripts/build-private-bundle.mjs            # write the artifact
 *   node scripts/build-private-bundle.mjs --check     # exit 1 on drift (CI)
 *   node scripts/build-private-bundle.mjs --json      # machine-readable summary
 */

import { createHash } from "node:crypto";
import { readFileSync, writeFileSync, existsSync, mkdirSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";

import yaml from "js-yaml";

const HERE = dirname(fileURLToPath(import.meta.url));
const FRONTEND = join(HERE, "..");
const REPO = join(FRONTEND, "..");
export const OUTPUT_PATH = join(FRONTEND, "public", "private", "safety-bundle.json");
export const CASEFOLD_FIXTURE = join(
  FRONTEND,
  "src",
  "features",
  "private",
  "unicode",
  "python-casefold.json",
);

/** The bundle format. Bump it when the shape changes; the loader refuses older. */
export const FORMAT = "manovia-private-bundle/1";

/** Loaded in this order — the same tuple as `patterns.PATTERN_FILES`. */
export const PATTERN_FILES = [
  "patterns_context.yaml",
  "patterns_core.yaml",
  "patterns_euphemisms.yaml",
  "patterns_indic.yaml",
  "patterns_redteam.yaml",
];
export const CONTEXT_FILE = "patterns_context.yaml";
export const OUTPUT_PATTERN_FILE = "output_patterns.yaml";
export const HELPLINES_FILE = "helplines.json";
export const LOCALE_FILES = ["en", "hi", "bn"];
/** The template ids `content/i18n/*.json` carries that Private Mode needs. */
export const CRISIS_TEMPLATE_IDS = [
  "crisis.high",
  "crisis.imminent",
  "crisis.about_someone_else",
  "check_in.medium",
  "check_in.low",
];

// --------------------------------------------------------------------------- //
// Constants mirrored from the Python engine.
//
// Every value here is asserted against its Python source by
// backend/tests/safety/test_private_bundle.py, so a change on either side that
// is not matched on the other fails a test instead of silently splitting the
// two engines. They are compiled into the bundle rather than written into the
// TypeScript so the browser reads the same numbers the server does.
// --------------------------------------------------------------------------- //

/** `normalise.DEFAULT_MAX_INPUT_CHARS` and friends. */
const ENGINE_NUMBERS = {
  max_input_chars: 4_000,
  max_backward_tokens: 8,
  max_transparent_skips: 5,
  max_forward_tokens: 3,
  subject_window: 3,
};

/** `rules.WINDOW_OVERLAP_CHARS`, `rules.MAX_WINDOWED_CHARS`. */
const WINDOWING = { window_overlap_chars: 256, max_windowed_chars: 64_000 };

/** `base.RiskLevel` — five ordered tiers; the order is the meaning. */
const LEVELS = { none: 0, low: 1, medium: 2, high: 3, imminent: 4 };

/**
 * `base.RiskLevel.to_stored()` — the four-tier database column (ADR 0008 §4).
 * HIGH and IMMINENT collapse onto CRISIS; LOW and MEDIUM are stored under their
 * Day-3 names, which are *not* the detection labels. Pinned against Python by
 * `backend/tests/safety/test_private_bundle.py`.
 */
const STORED_LEVELS = {
  none: "none",
  low: "caution",
  medium: "elevated",
  high: "crisis",
  imminent: "crisis",
};

/** `base.RiskCategory`. */
const CATEGORIES = [
  "suicidal_ideation",
  "self_harm",
  "intent_plan",
  "access_to_means",
  "harm_to_others",
  "abuse_disclosure",
  "severe_hopelessness",
  "acute_medical",
];

/** `rules.CODE_*` — the rationale codes the engine adds itself. */
const CODES = {
  negated: "ctx.negated",
  figurative: "ctx.figurative",
  third_person: "ctx.third_person",
  quoted: "ctx.quoted",
  fiction_frame: "ctx.fiction_frame",
  timeframe: "ctx.timeframe",
  empty_input: "input.empty",
  truncated: "input.truncated",
  windowed: "input.windowed",
};

/** `normalise._INVISIBLE`: deleted outright before anything else. */
const INVISIBLE = "\u200b\u200c\u200d\u200e\u200f\u2060\u2061\u2062\u2063\u2064\ufeff\u00ad\u180e";

/** `normalise._APOSTROPHES`: deleted, not spaced ("don't" and "dont" are one token). */
const APOSTROPHES = "\u0027\u2018\u2019\u02bc\u02b9\u00b4`";

/**
 * `normalise._MARKS`: the ranges that survive punctuation-stripping, because an
 * Indic combining mark is part of the word it belongs to.
 */
const KEPT_MARK_RANGES = [
  [0x0300, 0x036f],
  [0x0900, 0x097f],
  [0x0980, 0x09ff],
  [0x0a00, 0x0a7f],
  [0x0b00, 0x0b7f],
  [0x0c00, 0x0c7f],
];

/** `normalise._LATIN_COMBINING`: stacked Latin diacritics, Indic marks excluded. */
const LATIN_COMBINING_RANGES = [
  [0x0300, 0x036f],
  [0x1ab0, 0x1aff],
  [0x1dc0, 0x1dff],
  [0x20d0, 0x20ff],
  [0xfe20, 0xfe2f],
];

/** `normalise._CYRILLIC_LOOKALIKES` / `_GREEK_LOOKALIKES`, zipped as Python does. */
const CYRILLIC_LOOKALIKES = [
  "\u0430\u0432\u0435\u0451\u04bb\u0456\u0457\u0458\u043a\u04cf\u043c\u043d\u043e\u0440\u0441\u0455\u0442\u0443\u0445\u0501\u051b\u051d\u044c",
  "abeehiijklmhopcstyxdqwb",
];
const GREEK_LOOKALIKES = [
  "\u03b1\u03b2\u03b5\u03b7\u03b9\u03ba\u03bd\u03bf\u03c1\u03c4\u03c5\u03c7\u03c9",
  "abenikvoptuxw",
];

/** `normalise._CLAUSE_BAR`. */
/**
 * The system prompts the on-device model may be given.
 *
 * They ship in the bundle for one reason: the output guard's prompt-echo check has to
 * compare a reply against the prompt that was actually used, and a browser cannot read
 * `backend/app/content/prompts/`. Shipping the text also means the client cannot drift
 * onto an unreviewed prompt without the bundle's hash changing.
 */
const PROMPT_FILES = ["system_v1.md", "system_v2.md", "system_v3.md"];

const CLAUSE_BAR = "|";

/** `rules._FIRST_PERSON_SUBJECTS`. */
const FIRST_PERSON_SUBJECTS = ["i", "im", "ive", "id", "ill", "main", "mai", "ami"];

/** `rules._SELF_QUOTE`, verbatim. */
const SELF_QUOTE_PATTERN =
  "\\b(?:" +
  "i\\s+(?:just\\s+|once\\s+|had\\s+|have\\s+|already\\s+)?" +
  "(?:wrote|written|typed|said|posted|texted|tweeted|journaled|scribbled)" +
  "|(?:keep\\s+|kept\\s+)?(?:telling|told)\\s+myself" +
  "|to\\s+myself" +
  "|my\\s+(?:own\\s+)?(?:diary|journal|notes?|notebook|words|post|thoughts|status)" +
  "|what\\s+i\\s+(?:wrote|typed|said|posted)" +
  ")\\b";

/**
 * Python's `\s`, as an explicit code-point list: JavaScript's `\s` is a
 * different set (it matches U+FEFF and misses U+001C–U+001F and U+0085), and
 * the engine's punctuation-stripping regex is built from this class. Verified
 * exhaustively against `re.match(r"\s", …)` by the Python drift test.
 */
const WHITESPACE_CODEPOINTS = [
  9, 10, 11, 12, 13, 28, 29, 30, 31, 32, 133, 160, 5760, 8192, 8193, 8194, 8195, 8196, 8197, 8198,
  8199, 8200, 8201, 8202, 8232, 8233, 8239, 8287, 12288,
];

/**
 * Python's `\w` is exactly `[\p{L}\p{N}_]` — checked against every code point
 * by the Python drift test — and `\d` is exactly `\p{Nd}`. The browser
 * translator builds word boundaries from these.
 */
const WORD_CLASS_BODY = "\\p{L}\\p{N}_";
const DIGIT_CLASS = "\\p{Nd}";

/** `escalation._POLICIES`, spelled out. `allow_llm` false is AGENTS.md rule 1. */
const ESCALATION_POLICY = {
  none: {
    level: "none",
    template_id: null,
    about_others_template_id: null,
    actions: ["normal_reply"],
    allow_llm: true,
    deterministic_reply: false,
    show_helplines: false,
    show_emergency_instruction: false,
    record_event: false,
  },
  low: {
    level: "low",
    template_id: "check_in.low",
    about_others_template_id: null,
    actions: ["normal_reply", "supportive_check_in"],
    allow_llm: true,
    deterministic_reply: false,
    show_helplines: false,
    show_emergency_instruction: false,
    record_event: false,
  },
  medium: {
    level: "medium",
    template_id: "check_in.medium",
    about_others_template_id: null,
    actions: [
      "normal_reply",
      "supportive_check_in",
      "encourage_helpline",
      "show_helplines",
      "record_safety_event",
    ],
    allow_llm: true,
    deterministic_reply: false,
    show_helplines: true,
    show_emergency_instruction: false,
    record_event: true,
  },
  high: {
    level: "high",
    template_id: "crisis.high",
    about_others_template_id: "crisis.about_someone_else",
    actions: [
      "block_llm",
      "show_crisis_message",
      "show_emergency_instruction",
      "show_helplines",
      "record_safety_event",
    ],
    allow_llm: false,
    deterministic_reply: true,
    show_helplines: true,
    show_emergency_instruction: true,
    record_event: true,
  },
  imminent: {
    level: "imminent",
    template_id: "crisis.imminent",
    about_others_template_id: "crisis.about_someone_else",
    actions: [
      "block_llm",
      "show_crisis_message",
      "show_emergency_instruction",
      "show_helplines",
      "record_safety_event",
    ],
    allow_llm: false,
    deterministic_reply: true,
    show_helplines: true,
    show_emergency_instruction: true,
    record_event: true,
  },
};

/** `escalation.INTERNATIONAL_EMERGENCY_NUMBER`, `crisis.FALLBACK_REGION`, `i18n.FALLBACK_LOCALE`. */
const CRISIS_DEFAULTS = {
  international_emergency_number: "112",
  fallback_region: "DEFAULT",
  fallback_locale: "en",
};

// --------------------------------------------------------------------------- //
// Loading and validation
// --------------------------------------------------------------------------- //

/** Every problem found while compiling, so one run reports all of them. */
class CompileError extends Error {}

function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

function readSource(relativePath) {
  const absolute = join(REPO, relativePath);
  if (!existsSync(absolute)) {
    throw new CompileError(`missing source file: ${relativePath}`);
  }
  const bytes = readFileSync(absolute);
  return { relativePath, bytes, text: bytes.toString("utf8"), sha256: sha256(bytes) };
}

/**
 * Parse one YAML document the way PyYAML's `safe_load` would.
 *
 * `json: true` makes duplicate mapping keys last-wins instead of an error,
 * which is what PyYAML does. That tolerance is not blind: duplicates are
 * collected and reported, and
 * `backend/tests/safety/test_private_bundle.py` fails if a duplicated key ever
 * maps to two *different* values (today `patterns_context.yaml` repeats
 * `atmahatya` with the same value, so both loaders agree).
 */
function parseYaml(source, duplicates) {
  let strictError = null;
  try {
    yaml.load(source.text, { json: false });
  } catch (error) {
    strictError = error;
  }
  if (strictError !== null && /duplicated mapping key/.test(strictError.message)) {
    duplicates.push({ file: source.relativePath, message: strictError.message.split("\n")[0] });
  } else if (strictError !== null) {
    throw new CompileError(`${source.relativePath}: ${strictError.message}`);
  }
  const document = yaml.load(source.text, { json: true });
  if (document === null || typeof document !== "object" || Array.isArray(document)) {
    throw new CompileError(`${source.relativePath}: expected a mapping at the top level`);
  }
  return document;
}

const ID_RE = /^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+$/;
const LANGUAGE_RE = /^[a-z]{2}(?:-[a-z0-9]+)*$/;
const SCRIPTS = new Set(["devanagari", "bengali", "latin", "arabic"]);
const UPPERCASE_RE = /[A-Z]/;
const FORBIDDEN_IN_VALUE = new Set([...",.!?;:'\"(){}[]<>"]);
const PATTERN_LEVELS = new Set(["low", "medium", "high", "imminent"]);

const RULE_KEYS = new Set([
  "id",
  "category",
  "level",
  "kind",
  "value",
  "negation_sensitive",
  "negation_baked",
  "figurative_prone",
  "figurative_subject",
  "language",
  "script",
  "spelling",
  "note",
]);

const CONTEXT_KEYS = new Set([
  "version",
  "leet_map",
  "misspellings",
  "negation_cues",
  "negation_after_cues",
  "transparent_words",
  "third_person_markers",
  "third_person_patterns",
  "fiction_frames",
  "relation_possessives",
  "relation_nouns",
  "first_person_markers",
  "timeframe_markers",
  "benign_frames",
  "benign_subjects",
]);

const OUTPUT_RULE_KEYS = new Set([
  "id",
  "category",
  "kind",
  "value",
  "negation_sensitive",
  "script",
]);
const OUTPUT_TOP_KEYS = new Set([
  "version",
  "safe_replies",
  "trimmed_closings",
  "check_in_phrases",
  "negation_cues",
  "rules",
]);

/**
 * Validate one detection rule and pass it through unchanged.
 *
 * Mirrors `patterns._validate_rule`: a rule the Python loader would refuse must
 * also be refused here, so a bad content edit fails the frontend build with the
 * same message rather than shipping a pattern that can never match.
 */
function validateRule(raw, source, kindOf) {
  const where = `${source}:${kindOf}`;
  const unknown = Object.keys(raw)
    .filter((key) => !RULE_KEYS.has(key))
    .sort();
  if (unknown.length > 0) {
    throw new CompileError(`${where}: rule has unknown keys ${JSON.stringify(unknown)}`);
  }
  const id = raw.id;
  if (typeof id !== "string" || !ID_RE.test(id)) {
    throw new CompileError(
      `${where}: rule id ${JSON.stringify(id)} must look like 'group.snake_case'`,
    );
  }
  if (typeof raw.category !== "string" || !CATEGORIES.includes(raw.category)) {
    throw new CompileError(`${where}: ${id} has unknown category ${JSON.stringify(raw.category)}`);
  }
  if (typeof raw.level !== "string" || !PATTERN_LEVELS.has(raw.level)) {
    throw new CompileError(`${where}: ${id} has unknown level ${JSON.stringify(raw.level)}`);
  }
  if (raw.kind !== "phrase" && raw.kind !== "regex") {
    throw new CompileError(`${where}: ${id} has unknown kind ${JSON.stringify(raw.kind)}`);
  }
  const value = raw.value;
  if (typeof value !== "string" || value.trim() === "") {
    throw new CompileError(`${where}: ${id} has an empty value`);
  }
  if (UPPERCASE_RE.test(value)) {
    throw new CompileError(`${where}: ${id} must be written in lowercase (text is normalised)`);
  }
  if (raw.kind === "phrase" && [...value].some((char) => FORBIDDEN_IN_VALUE.has(char))) {
    throw new CompileError(
      `${where}: ${id} is a phrase containing punctuation that normalisation removes`,
    );
  }
  let language = raw.language ?? null;
  if (language !== null) {
    if (typeof language !== "string" || !LANGUAGE_RE.test(language)) {
      throw new CompileError(`${where}: ${id} has an invalid language ${JSON.stringify(language)}`);
    }
    language = language.trim().toLowerCase();
  }
  const script = raw.script ?? null;
  if (script !== null && !SCRIPTS.has(script)) {
    throw new CompileError(`${where}: ${id} has an unknown script ${JSON.stringify(script)}`);
  }
  const spelling = raw.spelling ?? null;
  if (spelling !== null && spelling !== "fused" && spelling !== "obfuscated") {
    throw new CompileError(
      `${where}: ${id} has an unknown spelling tag ${JSON.stringify(spelling)}`,
    );
  }
  return {
    id,
    category: raw.category,
    level: raw.level,
    kind: raw.kind,
    value,
    negation_sensitive: Boolean(raw.negation_sensitive ?? false),
    negation_baked: Boolean(raw.negation_baked ?? false),
    figurative_prone: Boolean(raw.figurative_prone ?? false),
    figurative_subject: Boolean(raw.figurative_subject ?? false),
    language,
    script,
    spelling,
  };
}

function strings(raw, key, source) {
  const items = raw[key] ?? [];
  if (!Array.isArray(items) || items.some((item) => typeof item !== "string")) {
    throw new CompileError(`${source}: ${key} must be a list of strings`);
  }
  return [...items];
}

function stringMap(raw, key, source) {
  const value = raw[key] ?? {};
  if (
    value === null ||
    typeof value !== "object" ||
    Array.isArray(value) ||
    Object.entries(value).some(
      ([mapKey, mapValue]) => typeof mapKey !== "string" || typeof mapValue !== "string",
    )
  ) {
    throw new CompileError(`${source}: ${key} must map strings to strings`);
  }
  return { ...value };
}

/** `patterns._compile_context`, minus the compilation (the browser compiles). */
function validateContext(raw, source) {
  const unknown = Object.keys(raw)
    .filter((key) => !CONTEXT_KEYS.has(key))
    .sort();
  if (unknown.length > 0) {
    throw new CompileError(`${source}: unknown context keys ${JSON.stringify(unknown)}`);
  }
  // Python compiles the misspelling map longest-source-first (a stable sort, so
  // equal lengths keep their YAML order) and the browser must apply them in that
  // same order: `suicidalthoughts` has to be fixed before `sucide` can rewrite
  // part of it. Shipping the ordered pairs instead of an object also keeps the
  // order out of the hands of JSON key ordering, which is not insertion order for
  // integer-like keys.
  const misspellings = Object.entries(stringMap(raw, "misspellings", source))
    .map(([key, value], index) => ({ key, value, index }))
    .sort((left, right) => right.key.length - left.key.length || left.index - right.index)
    .map(({ key, value }) => [key, value]);

  return {
    leet_map: stringMap(raw, "leet_map", source),
    misspellings,
    negation_cues: strings(raw, "negation_cues", source),
    negation_after_cues: strings(raw, "negation_after_cues", source),
    transparent_words: strings(raw, "transparent_words", source),
    third_person_markers: strings(raw, "third_person_markers", source),
    third_person_patterns: strings(raw, "third_person_patterns", source),
    fiction_frames: strings(raw, "fiction_frames", source),
    relation_possessives: strings(raw, "relation_possessives", source),
    relation_nouns: strings(raw, "relation_nouns", source),
    first_person_markers: strings(raw, "first_person_markers", source),
    timeframe_markers: strings(raw, "timeframe_markers", source),
    benign_frames: strings(raw, "benign_frames", source),
    benign_subjects: strings(raw, "benign_subjects", source),
  };
}

/** `output_guard._compile_rules`: the same shape checks over the reply-side rules. */
function validateOutputGuard(raw, source) {
  const unknown = Object.keys(raw)
    .filter((key) => !OUTPUT_TOP_KEYS.has(key))
    .sort();
  if (unknown.length > 0) {
    throw new CompileError(`${source}: unknown top-level keys ${JSON.stringify(unknown)}`);
  }
  const rules = (raw.rules ?? []).map((entry) => {
    const extra = Object.keys(entry)
      .filter((key) => !OUTPUT_RULE_KEYS.has(key))
      .sort();
    if (extra.length > 0) {
      throw new CompileError(`${source}: output rule has unknown keys ${JSON.stringify(extra)}`);
    }
    if (typeof entry.id !== "string" || !ID_RE.test(entry.id)) {
      throw new CompileError(
        `${source}: output rule id ${JSON.stringify(entry.id)} is not 'group.snake_case'`,
      );
    }
    if (typeof entry.category !== "string" || entry.category.trim() === "") {
      throw new CompileError(`${source}: ${entry.id} has no category`);
    }
    if (entry.kind !== "phrase" && entry.kind !== "regex") {
      throw new CompileError(
        `${source}: ${entry.id} has unknown kind ${JSON.stringify(entry.kind)}`,
      );
    }
    if (typeof entry.value !== "string" || entry.value.trim() === "") {
      throw new CompileError(`${source}: ${entry.id} has an empty value`);
    }
    if (UPPERCASE_RE.test(entry.value)) {
      throw new CompileError(`${source}: ${entry.id} must be lowercase (text is normalised)`);
    }
    return {
      id: entry.id,
      category: entry.category,
      kind: entry.kind,
      value: entry.value,
      negation_sensitive: Boolean(entry.negation_sensitive ?? false),
      script: entry.script ?? null,
    };
  });
  for (const key of ["safe_replies", "trimmed_closings"]) {
    const block = raw[key] ?? {};
    for (const [locale, value] of Object.entries(block)) {
      if (typeof value !== "string") {
        throw new CompileError(`${source}: ${key}.${locale} must be a string`);
      }
    }
  }
  return {
    rules,
    safe_replies: Object.fromEntries(
      Object.entries(raw.safe_replies ?? {}).filter(([locale]) => LOCALE_FILES.includes(locale)),
    ),
    trimmed_closings: Object.fromEntries(
      Object.entries(raw.trimmed_closings ?? {}).filter(([locale]) =>
        LOCALE_FILES.includes(locale),
      ),
    ),
    check_in_phrases: strings(raw, "check_in_phrases", source),
    negation_cues: strings(raw, "negation_cues", source),
  };
}

/**
 * `crisis.CrisisResource.tel_href` / `sms_href`, computed here so the dialling
 * format still lives in exactly one place — the same rule, ported.
 */
function contactHrefs(resource) {
  const cleaned = (resource.number ?? "").replace(/[^\d+]/g, "");
  const tel =
    resource.number !== null && resource.type === "call" && cleaned !== ""
      ? `tel:${cleaned}`
      : null;
  const sms =
    resource.number !== null && resource.type === "text" && cleaned !== ""
      ? `sms:${cleaned}`
      : null;
  return { tel_href: tel, sms_href: sms };
}

function validateHelplines(raw, source) {
  if (raw.version !== 2) {
    throw new CompileError(
      `${source}: unsupported helplines version ${JSON.stringify(raw.version)}`,
    );
  }
  if (!Array.isArray(raw.regions) || raw.regions.length === 0) {
    throw new CompileError(`${source}: regions must be a non-empty list`);
  }
  if (!raw.regions.includes(CRISIS_DEFAULTS.fallback_region)) {
    throw new CompileError(`${source}: must declare a ${CRISIS_DEFAULTS.fallback_region} region`);
  }
  if (!Array.isArray(raw.resources) || raw.resources.length === 0) {
    throw new CompileError(`${source}: resources must be a non-empty list`);
  }
  const ids = raw.resources.map((resource) => resource.id);
  if (new Set(ids).size !== ids.length) {
    throw new CompileError(`${source}: duplicate resource ids`);
  }
  const resources = raw.resources.map((resource) => ({
    id: resource.id,
    region: resource.region,
    kind: resource.kind,
    name: resource.name,
    number: resource.number ?? null,
    type: resource.type,
    text_keyword: resource.text_keyword ?? null,
    instructions: resource.instructions ?? null,
    hours: resource.hours,
    languages: resource.languages,
    audience: resource.audience ?? null,
    description: resource.description,
    url: resource.url ?? null,
    source_url: resource.source_url,
    last_verified: resource.last_verified,
    needs_verification: Boolean(resource.needs_verification ?? false),
    verification_note: resource.verification_note ?? null,
    priority: resource.priority,
    ...contactHrefs(resource),
  }));
  for (const resource of resources) {
    if (!raw.regions.includes(resource.region)) {
      throw new CompileError(`${source}: resource ${resource.id} declares an unknown region`);
    }
    if (resource.number === null && resource.url === null) {
      throw new CompileError(`${source}: resource ${resource.id} has no way to make contact`);
    }
  }
  for (const region of raw.regions) {
    const hasEmergency = resources.some(
      (resource) => resource.region === region && resource.kind === "emergency" && resource.number,
    );
    if (!hasEmergency) {
      throw new CompileError(`${source}: region ${region} has no emergency number`);
    }
  }
  return {
    version: raw.version,
    last_verified: raw.last_verified,
    source: raw.source,
    disclaimer: raw.disclaimer,
    regions: [...raw.regions],
    resources,
  };
}

function validateLocales(sources) {
  const locales = {};
  for (const locale of LOCALE_FILES) {
    const source = sources[`content/i18n/${locale}.json`];
    const raw = JSON.parse(source.text);
    if (raw.locale !== locale) {
      throw new CompileError(
        `${source.relativePath}: locale field is ${JSON.stringify(raw.locale)}`,
      );
    }
    const whitelist = raw.placeholder_whitelist;
    if (!Array.isArray(whitelist)) {
      throw new CompileError(`${source.relativePath}: placeholder_whitelist must be a list`);
    }
    const templates = {};
    for (const templateId of CRISIS_TEMPLATE_IDS) {
      const template = (raw.templates ?? {})[templateId];
      if (template === undefined) {
        throw new CompileError(`${source.relativePath}: missing template ${templateId}`);
      }
      if (
        typeof template.title !== "string" ||
        !Array.isArray(template.body) ||
        template.body.length === 0
      ) {
        throw new CompileError(`${source.relativePath}: ${templateId} is not a usable template`);
      }
      templates[templateId] = {
        title: template.title,
        body: [...template.body],
        helpline_intro: template.helpline_intro ?? null,
        emergency_instruction: template.emergency_instruction ?? null,
        trusted_person: template.trusted_person ?? null,
        safety_steps: [...(template.safety_steps ?? [])],
        closing: template.closing ?? null,
        disclaimer: template.disclaimer ?? null,
      };
    }
    locales[locale] = { locale, placeholder_whitelist: [...whitelist], templates };
  }
  return locales;
}

// --------------------------------------------------------------------------- //
// The Unicode tables the browser cannot derive for itself
// --------------------------------------------------------------------------- //

function codepoints(text) {
  return [...new Set([...text].map((char) => char.codePointAt(0)))].sort(
    (left, right) => left - right,
  );
}

function confusables() {
  const map = {};
  for (const [sources, targets] of [CYRILLIC_LOOKALIKES, GREEK_LOOKALIKES]) {
    const from = [...sources];
    const to = [...targets];
    if (from.length !== to.length) {
      throw new CompileError("look-alike tables are not the same length");
    }
    from.forEach((char, index) => {
      map[String(char.codePointAt(0))] = to[index];
    });
  }
  return map;
}

/**
 * Derive the casefold overrides from the Python-exported fixture.
 *
 * Nothing is hard-coded: for every code point Python folds (or deliberately does
 * not fold, but a browser would lowercase), compare with this runtime's
 * `toLowerCase()` and keep only the divergences. The result is exactly the table
 * `src/features/private/rules/casefold.ts` needs to reproduce
 * `str.casefold()`.
 */
export function deriveCasefoldOverrides(fixture = readFileSync(CASEFOLD_FIXTURE, "utf8")) {
  const parsed = JSON.parse(fixture);
  if (parsed.format !== "manovia-casefold-cases/1") {
    throw new CompileError(`unexpected casefold fixture format ${JSON.stringify(parsed.format)}`);
  }
  const overrides = {};
  for (const [hexCodepoint, folded] of Object.entries(parsed.cases)) {
    const codepoint = Number.parseInt(hexCodepoint, 16);
    const char = String.fromCodePoint(codepoint);
    if (char.toLowerCase() !== folded) {
      overrides[String(codepoint)] = folded;
    }
  }
  return {
    overrides,
    fixture_count: parsed.count,
    unicodedata_version: parsed.unicodedata_version,
  };
}

/**
 * Exhaustive completeness check for the casefold fixture.
 *
 * For every code point *not* in the fixture, `toLowerCase()` must be the
 * identity — otherwise the browser would fold a character Python leaves alone
 * and the two engines would disagree on that message. Slow on purpose
 * (~1.1M iterations); the build calls it only with `--verify-unicode`, and
 * `casefold.test.ts` runs it on every test pass.
 */
export function verifyUnicodeClaim(fixture = readFileSync(CASEFOLD_FIXTURE, "utf8")) {
  const cases = JSON.parse(fixture).cases;
  const missing = [];
  for (let codepoint = 0; codepoint <= 0x10ffff; codepoint += 1) {
    if (codepoint >= 0xd800 && codepoint <= 0xdfff) continue;
    if (`${"0x"}${codepoint.toString(16)}` in cases) continue;
    const char = String.fromCodePoint(codepoint);
    if (char.toLowerCase() !== char) {
      missing.push(codepoint);
    }
  }
  return missing;
}

// --------------------------------------------------------------------------- //
// Compile
// --------------------------------------------------------------------------- //

export function compileBundle({ verifyUnicode = false } = {}) {
  const relativeSources = [
    ...PATTERN_FILES.map((name) => `backend/app/content/safety/${name}`),
    `backend/app/content/safety/${OUTPUT_PATTERN_FILE}`,
    `backend/app/content/${HELPLINES_FILE}`,
    ...LOCALE_FILES.map((locale) => `backend/app/content/i18n/${locale}.json`),
    ...PROMPT_FILES.map((name) => `backend/app/content/prompts/${name}`),
  ];
  const sources = Object.fromEntries(
    relativeSources.map((path) => [path.replace(/^backend\/app\//, ""), readSource(path)]),
  );

  const duplicates = [];
  const documents = {};
  for (const name of [...PATTERN_FILES, OUTPUT_PATTERN_FILE]) {
    documents[name] = parseYaml(sources[`content/safety/${name}`], duplicates);
  }

  // Rules, in the same order Python loads them (context first, and it carries
  // no rules of its own).
  const rules = [];
  for (const name of PATTERN_FILES) {
    if (name === CONTEXT_FILE) continue;
    const entries = documents[name].rules ?? [];
    if (!Array.isArray(entries)) {
      throw new CompileError(`${name}: 'rules' must be a list`);
    }
    for (const entry of entries) {
      rules.push(validateRule(entry, name, "rule"));
    }
  }
  const ids = rules.map((rule) => rule.id);
  const duplicatedIds = [
    ...new Set(ids.filter((id) => ids.filter((other) => other === id).length > 1)),
  ].sort();
  if (duplicatedIds.length > 0) {
    throw new CompileError(
      `pattern ids must be unique; duplicated: ${JSON.stringify(duplicatedIds)}`,
    );
  }
  if (rules.length === 0) {
    throw new CompileError("the pattern set contains no rules — refusing to ship a blind engine");
  }

  const context = validateContext(documents[CONTEXT_FILE], CONTEXT_FILE);
  const outputGuard = validateOutputGuard(documents[OUTPUT_PATTERN_FILE], OUTPUT_PATTERN_FILE);
  const helplines = validateHelplines(
    JSON.parse(sources[`content/${HELPLINES_FILE}`].text),
    HELPLINES_FILE,
  );
  const locales = validateLocales(sources);
  const casefold = deriveCasefoldOverrides();

  if (verifyUnicode) {
    const missing = verifyUnicodeClaim();
    if (missing.length > 0) {
      throw new CompileError(
        `casefold fixture is incomplete: ${missing.length} code points lowercase in a browser but are ` +
          `missing, e.g. ${missing
            .slice(0, 8)
            .map((cp) => `0x${cp.toString(16)}`)
            .join(", ")}`,
      );
    }
  }

  const bundle = {
    format: FORMAT,
    generated_by: "frontend/scripts/build-private-bundle.mjs",
    sources: Object.fromEntries(
      Object.entries(sources).map(([key, source]) => [
        key,
        { sha256: source.sha256, bytes: source.bytes.length },
      ]),
    ),
    duplicates_tolerated: duplicates,
    unicode: {
      whitespace_codepoints: WHITESPACE_CODEPOINTS,
      word_class_body: WORD_CLASS_BODY,
      digit_class: DIGIT_CLASS,
      invisible_codepoints: codepoints(INVISIBLE),
      apostrophe_codepoints: codepoints(APOSTROPHES),
      kept_mark_ranges: KEPT_MARK_RANGES,
      latin_combining_ranges: LATIN_COMBINING_RANGES,
      confusables: confusables(),
      casefold_overrides: casefold.overrides,
      casefold_fixture: {
        count: casefold.fixture_count,
        unicodedata_version: casefold.unicodedata_version,
      },
    },
    engine: {
      ...ENGINE_NUMBERS,
      ...WINDOWING,
      clause_bar: CLAUSE_BAR,
      levels: LEVELS,
      stored_levels: STORED_LEVELS,
      categories: CATEGORIES,
      codes: CODES,
      first_person_subjects: FIRST_PERSON_SUBJECTS,
      self_quote_pattern: SELF_QUOTE_PATTERN,
      rules,
      context,
    },
    prompts: Object.fromEntries(
      PROMPT_FILES.map((name) => {
        const key = `content/prompts/${name}`;
        const source = sources[key];
        if (source === undefined) {
          throw new CompileError(`missing prompt source: ${key}`);
        }
        return [
          name.replace(/\.md$/, ""),
          {
            path: key,
            sha256: source.sha256,
            bytes: source.bytes.length,
            words: source.text.split(/\s+/).filter(Boolean).length,
            text: source.text,
          },
        ];
      }),
    ),
    output_guard: outputGuard,
    crisis: {
      ...CRISIS_DEFAULTS,
      policy: ESCALATION_POLICY,
      locales,
      helplines,
    },
  };

  const rendered = `${renderCanonical(bundle)}\n`;
  return {
    bundle,
    rendered,
    sha256: sha256(rendered),
    bytes: Buffer.byteLength(rendered, "utf8"),
    counts: {
      rules: rules.length,
      output_rules: outputGuard.rules.length,
      negation_cues: context.negation_cues.length,
      misspellings: Object.keys(context.misspellings).length,
      leet_map: Object.keys(context.leet_map).length,
      benign_frames: context.benign_frames.length,
      third_person_markers: context.third_person_markers.length,
      helplines: helplines.resources.length,
      locales: LOCALE_FILES.length,
      casefold_overrides: Object.keys(casefold.overrides).length,
    },
    duplicates,
  };
}

/**
 * Serialise with object keys sorted at every level, so the artifact is a
 * function of the sources alone: no key-order churn between Node versions, and
 * `--check` can compare bytes.
 */
export function renderCanonical(value) {
  return JSON.stringify(sortDeep(value));
}

function sortDeep(value) {
  if (Array.isArray(value)) {
    return value.map(sortDeep);
  }
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .map((key) => [key, sortDeep(value[key])]),
    );
  }
  return value;
}

export function writeBundle(result, outputPath = OUTPUT_PATH) {
  mkdirSync(dirname(outputPath), { recursive: true });
  writeFileSync(outputPath, result.rendered, "utf8");
  return outputPath;
}

export function checkBundle(result, outputPath = OUTPUT_PATH) {
  if (!existsSync(outputPath)) {
    return { ok: false, reason: `missing ${relative(REPO, outputPath)}` };
  }
  const current = readFileSync(outputPath, "utf8");
  if (current !== result.rendered) {
    return {
      ok: false,
      reason: `${relative(REPO, outputPath)} differs from a fresh compile (sha256 ${sha256(current)} vs ${result.sha256})`,
    };
  }
  return { ok: true, reason: "identical" };
}

function main(argv) {
  const check = argv.includes("--check");
  const asJson = argv.includes("--json");
  const verifyUnicode =
    argv.includes("--verify-unicode") || process.env.PRIVATE_BUNDLE_VERIFY_UNICODE === "1";
  let result;
  try {
    result = compileBundle({ verifyUnicode });
  } catch (error) {
    const message =
      error instanceof CompileError ? error.message : `${error.name}: ${error.message}`;
    if (asJson) {
      process.stdout.write(`${JSON.stringify({ ok: false, error: message })}\n`);
    } else {
      process.stderr.write(`private bundle compile failed: ${message}\n`);
    }
    return 1;
  }

  if (check) {
    const verdict = checkBundle(result);
    if (asJson) {
      process.stdout.write(
        `${JSON.stringify({ ok: verdict.ok, ...verdict, ...summary(result) })}\n`,
      );
    } else if (!verdict.ok) {
      process.stderr.write(`DRIFT ${verdict.reason}\n`);
    } else {
      process.stdout.write(`OK ${summaryLine(result)}\n`);
    }
    return verdict.ok ? 0 : 1;
  }

  const written = writeBundle(result);
  if (asJson) {
    process.stdout.write(
      `${JSON.stringify({ ok: true, path: relative(REPO, written), ...summary(result) })}\n`,
    );
  } else {
    process.stdout.write(
      `wrote ${relative(REPO, written)}\n${summaryLine(result)}\n` +
        (result.duplicates.length > 0
          ? `${result.duplicates
              .map(
                (entry) =>
                  `warning: duplicate YAML key tolerated (last wins, as PyYAML): ${entry.file}`,
              )
              .join("\n")}\n`
          : ""),
    );
  }
  return 0;
}

function summary(result) {
  return {
    sha256: result.sha256,
    bytes: result.bytes,
    counts: result.counts,
    duplicates_tolerated: result.duplicates,
  };
}

function summaryLine(result) {
  const counts = result.counts;
  return (
    `${(result.bytes / 1024).toFixed(1)} KB, sha256 ${result.sha256.slice(0, 16)} — ` +
    `${counts.rules} rules, ${counts.output_rules} output rules, ${counts.helplines} helplines, ` +
    `${counts.locales} locales, ${counts.casefold_overrides} casefold overrides`
  );
}

if (process.argv[1] !== undefined && fileURLToPath(import.meta.url) === process.argv[1]) {
  process.exitCode = main(process.argv.slice(2));
}
