/**
 * Python regular expressions, in a browser.
 *
 * Every detection rule in `content/safety/*.yaml` is written for Python's `re`,
 * and the bundle ships those sources verbatim (so
 * `backend/tests/safety/test_private_bundle.py` can compare them with the file).
 * This module is the translator that makes them mean the same thing here.
 *
 * Four differences actually matter, and each one is a silent miss if ignored:
 *
 * 1. **`\w`, `\d` and `\s` are Unicode-aware in Python and ASCII-only in
 *    JavaScript.** `\w` becomes `[\p{L}\p{N}_]` — proved equal over every code
 *    point by the Python drift test — `\d` becomes `\p{Nd}`, and `\s` becomes an
 *    explicit class built from Python's 29 whitespace code points (JavaScript's
 *    `\s` additionally matches U+FEFF and misses U+001C–U+001F and U+0085).
 * 2. **`\b` is defined in terms of `\w`**, so it cannot simply be kept: it is
 *    expanded to the boundary it means — exactly one side is a word character —
 *    using lookarounds. The general form is used everywhere rather than a
 *    "start of pattern" special case, because a wrong boundary is a wrong
 *    verdict. Measured cost: 215 rules over 500 messages in ~0.7s in Node, and
 *    ~1ms for one message across every rule, which is what the chat needs.
 * 3. **Unicode mode is stricter about escapes.** `\ ` and `\,` are legal in
 *    Python and a syntax error in a JavaScript `u`-flag pattern, so identity
 *    escapes of punctuation are unwritten.
 * 4. **`(?P<name>)` and `(?P=name)`** are Python's spelling of named groups.
 *    Nothing in the shipped content uses them; they are translated anyway, and
 *    anything else unsupported fails loudly at load rather than never matching.
 *
 * Failing loudly matters more than translating cleverly: a safety pattern that
 * silently stops matching is the one failure mode this feature must not have.
 */

import { codePointsToClassBody, rangesToClassBody, wordClassBody } from "./pyStrings";
import type { UnicodeTables } from "./bundleSchema";

/** JavaScript syntax characters, which must be escaped in a literal. */
const JS_SYNTAX_CHARS = new Set([
  "^",
  "$",
  "\\",
  ".",
  "*",
  "+",
  "?",
  "(",
  ")",
  "[",
  "]",
  "{",
  "}",
  "|",
  "/",
]);

/** Python's `re.escape` set (3.7+): it escapes punctuation, not letters. */
const PY_IDENTITY_ESCAPES = new Set([
  ...JS_SYNTAX_CHARS,
  // Python's `re.escape` escapes whitespace as itself (`\ `); a `u`-flag
  // JavaScript pattern refuses that escape, and the character needs none.
  " ",
  "\t",
  "\n",
  "\r",
  "\v",
  "\f",
  "-",
  "&",
  "~",
  "#",
  ",",
  ":",
  ";",
  "<",
  ">",
  "=",
  "!",
  "@",
  "%",
  "'",
  '"',
  "`",
  "*",
  "/",
]);

/** A class body matching one Python word character. */
function wordBody(tables: UnicodeTables): string {
  return wordClassBody(tables);
}

/** Python's `\s` as a class body (no brackets). */
function whitespaceBody(tables: UnicodeTables): string {
  return codePointsToClassBody(tables.whitespace_codepoints);
}

/** The Indic and combining-mark ranges normalisation keeps. */
export function marksBody(tables: UnicodeTables): string {
  return rangesToClassBody(tables.kept_mark_ranges);
}

/** `(?<!W)` / `(?=W)` pieces, built once per bundle. */
function boundaryForms(tables: UnicodeTables): { boundary: string; nonBoundary: string } {
  const word = `[${wordBody(tables)}]`;
  return {
    boundary: `(?:(?<=${word})(?!${word})|(?<!${word})(?=${word}))`,
    nonBoundary: `(?:(?<=${word})(?=${word})|(?<!${word})(?!${word}))`,
  };
}

/** Raised when a shipped pattern cannot be expressed in JavaScript. */
export class PatternTranslationError extends Error {
  readonly source: string;
  readonly reason: string;

  constructor(source: string, reason: string) {
    super(`cannot translate Python pattern ${JSON.stringify(source)}: ${reason}`);
    this.name = "PatternTranslationError";
    this.source = source;
    this.reason = reason;
  }
}

/**
 * Translate one Python pattern source into a JavaScript one.
 *
 * The result is meant to be compiled with the `u` flag; the caller adds `g` when
 * it needs every match (`finditer`).
 */
export function translatePythonRegex(source: string, tables: UnicodeTables): string {
  const { boundary, nonBoundary } = boundaryForms(tables);
  const whitespace = whitespaceBody(tables);
  let out = "";
  let inClass = false;
  let index = 0;

  while (index < source.length) {
    const char = source[index] as string;

    if (char === "\\") {
      const next = source[index + 1];
      if (next === undefined) {
        throw new PatternTranslationError(source, "pattern ends with a backslash");
      }
      index += 2;

      if (inClass && next === "b") {
        out += "\\u0008"; // backspace inside a class, in both engines
        continue;
      }
      switch (next) {
        case "b":
          out += inClass ? "\\u0008" : boundary;
          continue;
        case "B":
          if (inClass) {
            throw new PatternTranslationError(source, "\\B is not valid inside a character class");
          }
          out += nonBoundary;
          continue;
        case "w":
          out += inClass ? wordBody(tables) : `[${wordBody(tables)}]`;
          continue;
        case "W":
          if (inClass) {
            throw new PatternTranslationError(source, "\\W inside a class needs the v flag");
          }
          out += `[^${wordBody(tables)}]`;
          continue;
        case "d":
          out += inClass ? "\\p{Nd}" : "\\p{Nd}";
          continue;
        case "D":
          if (inClass) {
            throw new PatternTranslationError(source, "\\D inside a class needs the v flag");
          }
          out += "\\P{Nd}";
          continue;
        case "s":
          out += inClass ? whitespace : `[${whitespace}]`;
          continue;
        case "S":
          if (inClass) {
            throw new PatternTranslationError(source, "\\S inside a class needs the v flag");
          }
          out += `[^${whitespace}]`;
          continue;
        case "A":
          out += inClass ? "\\A" : "^";
          continue;
        case "Z":
          out += inClass ? "\\Z" : "$";
          continue;
        default:
          break;
      }

      if (/[a-zA-Z0-9]/.test(next)) {
        // A character escape both engines understand (\n, \t, \u1234, \x41…).
        out += `\\${next}`;
        continue;
      }
      if (JS_SYNTAX_CHARS.has(next)) {
        out += `\\${next}`;
        continue;
      }
      if (PY_IDENTITY_ESCAPES.has(next)) {
        // Legal in Python, a syntax error in a `u`-flag pattern: unescape it.
        out += next;
        continue;
      }
      throw new PatternTranslationError(source, `unsupported escape \\${next}`);
    }

    if (char === "[") {
      if (inClass) {
        // A literal `[` inside a class: legal in Python, must be escaped here.
        out += "\\[";
        index += 1;
        continue;
      }
      if (source[index + 1] === "]") {
        // `[]]` means "a class containing ]" in Python and "an empty class" in
        // JavaScript — a silent never-match. Nothing in the shipped content does
        // this, and guessing would be worse than refusing.
        throw new PatternTranslationError(
          source,
          "a class that starts with ] means different things in Python and JavaScript",
        );
      }
      inClass = true;
      out += "[";
      index += 1;
      continue;
    }
    if (char === "]" && inClass) {
      inClass = false;
      out += "]";
      index += 1;
      continue;
    }
    if (!inClass && char === "(" && source.startsWith("(?P<", index)) {
      out += "(?<";
      index += 4;
      continue;
    }
    if (!inClass && source.startsWith("(?P=", index)) {
      // Python's backreference spelling: `(?P=name)` is `\k<name>` here.
      const end = source.indexOf(")", index);
      if (end === -1) {
        throw new PatternTranslationError(source, "unterminated (?P=name)");
      }
      out += `\\k<${source.slice(index + 4, end)}>`;
      index = end + 1;
      continue;
    }
    if (!inClass && (char === "{" || char === "}")) {
      // `{0,2}` is a quantifier in both engines and must survive; a *literal*
      // brace is tolerated by Python and refused by a `u`-flag pattern, so only
      // that one gets escaped. Escaping the quantifier too — which an earlier
      // draft did — turns `(?:\w+\s+){0,2}` into a string nobody ever types,
      // and the rule silently stops matching.
      const quantifier = /^\{\d+(?:,\d*)?\}/.exec(source.slice(index));
      if (char === "{" && quantifier !== null) {
        out += quantifier[0];
        index += quantifier[0].length;
        continue;
      }
      out += `\\${char}`;
      index += 1;
      continue;
    }
    out += char;
    index += 1;
  }

  if (inClass) {
    throw new PatternTranslationError(source, "unterminated character class");
  }
  return out;
}

/** Escape a literal for a JavaScript `u`-flag pattern (Python's `re.escape`). */
export function escapeLiteral(text: string): string {
  let out = "";
  for (const char of text) {
    out += JS_SYNTAX_CHARS.has(char) ? `\\${char}` : char;
  }
  return out;
}

/** Escape a literal for use *inside* a JavaScript character class. */
export function escapeForClass(text: string): string {
  let out = "";
  for (const char of text) {
    out += JS_SYNTAX_CHARS.has(char) || char === "-" ? `\\${char}` : char;
  }
  return out;
}

/**
 * `patterns.collapse_repeats`: runs of two or more identical word characters
 * become one (`diiiie` → `die`, and `kill` → `kil` on both sides of the
 * comparison, which is what keeps it honest).
 */
export function collapseRepeats(tables: UnicodeTables, value: string): string {
  return value.replace(new RegExp(`([${wordBody(tables)}])\\1+`, "gu"), "$1");
}

/** True when every code point is ASCII (`str.isascii()`). */
export function isAscii(text: string): boolean {
  for (const char of text) {
    if ((char.codePointAt(0) ?? 0) > 0x7f) {
      return false;
    }
  }
  return true;
}

/** One compiled matcher, plus what it was built from. */
export interface CompiledMatcher {
  /** Test one string — `pattern.search(text) is not None`. */
  test(text: string): boolean;
  /** Every match, with offsets — `re.finditer`. */
  find(text: string): RegExpMatchArray[];
  /**
   * Replace every match — `pattern.sub(replacement, text)`.
   *
   * The replacement goes through a function, so a `$` in it can never be read as
   * a group reference the way `String.replace` would read it.
   */
  replace(text: string, replacement: string): string;
  /** The JavaScript source, for diagnostics and tests. */
  source: string;
}

function compile(source: string, flags: string): CompiledMatcher {
  let single: RegExp;
  let global: RegExp;
  try {
    single = new RegExp(source, flags);
    global = new RegExp(source, `${flags}g`);
  } catch (error) {
    // Named here rather than thrown bare, so the message at bundle load says
    // which pattern failed instead of leaving a SyntaxError to be guessed at.
    throw new PatternTranslationError(
      source,
      error instanceof Error ? error.message : String(error),
    );
  }
  return {
    test: (text: string): boolean => single.test(text),
    find: (text: string): RegExpMatchArray[] => [...text.matchAll(global)],
    replace: (text: string, replacement: string): string => text.replace(global, () => replacement),
    source,
  };
}

/** Compile a translated Python pattern, with the `u` flag always on. */
export function compileTranslated(source: string, tables: UnicodeTables): CompiledMatcher {
  return compile(translatePythonRegex(source, tables), "u");
}

/**
 * `patterns._compile_phrase`.
 *
 * An ASCII phrase matches on word boundaries with flexible internal whitespace
 * (`want to die` also matches `want  to   die`); a phrase in another script
 * matches as a substring, because a boundary assertion inside a word written with
 * combining marks would never fire. The two extra projections — whitespace
 * removed and repeated letters collapsed — are what catch `wanttodie` and
 * `diiiie`, and both sides of the comparison are mangled identically.
 */
export function compilePhrase(
  value: string,
  tables: UnicodeTables,
): {
  pattern: CompiledMatcher;
  squashed: CompiledMatcher | null;
  collapsed: CompiledMatcher | null;
} {
  const whitespace = `[${whitespaceBody(tables)}]+`;
  const word = `[${wordBody(tables)}]`;
  let pattern: CompiledMatcher;
  let squashed: CompiledMatcher | null = null;

  if (isAscii(value)) {
    const body = value
      .split(/([ \t\n\r\f\v]+)/)
      .map((part) => (/^[ \t\n\r\f\v]+$/.test(part) ? whitespace : escapeLiteral(part)))
      .join("");
    pattern = compile(`(?<!${word})${body}(?!${word})`, "u");
    const fused = value.replace(new RegExp(whitespace, "gu"), "");
    if (fused !== value) {
      squashed = compile(escapeLiteral(fused), "u");
    }
  } else {
    pattern = compile(escapeLiteral(value), "u");
  }

  const collapsedValue = collapseRepeats(tables, value);
  const collapsed = collapsedValue === value ? null : compile(escapeLiteral(collapsedValue), "u");
  return { pattern, squashed, collapsed };
}

/**
 * `normalise._marker_pattern`: one alternation over a marker list, longest first,
 * on word boundaries.
 *
 * Longest first so `my best friend` wins over `my`. Equal-length markers are
 * additionally ordered lexicographically here, which Python does not do (it
 * sorts a set, whose order is per-process); the result of the alternation cannot
 * depend on that order, because two different markers of the same length cannot
 * both match at the same position — but a deterministic bundle is a reviewable
 * one.
 */
export function compileMarkerPattern(
  markers: readonly string[],
  tables: UnicodeTables,
): CompiledMatcher {
  const ordered = [...new Set(markers.map((marker) => marker.trim().toLowerCase()))]
    .filter((marker) => marker !== "")
    .sort(
      (left, right) => right.length - left.length || (left < right ? -1 : left > right ? 1 : 0),
    );
  if (ordered.length === 0) {
    return compile("(?!)", "u"); // never matches, as Python does
  }
  const whitespace = `[${whitespaceBody(tables)}]+`;
  const word = `[${wordBody(tables)}]`;
  const body = ordered
    .map((marker) =>
      marker
        .split(/([ \t\n\r\f\v]+)/)
        .map((part) => (/^[ \t\n\r\f\v]+$/.test(part) ? whitespace : escapeLiteral(part)))
        .join(""),
    )
    .join("|");
  return compile(`(?<!${word})(?:${body})(?!${word})`, "u");
}

/** `normalise._relation_pattern`: `<possessive> <relation noun>` as one phrase. */
export function compileRelationPattern(
  possessives: readonly string[],
  nouns: readonly string[],
  tables: UnicodeTables,
): CompiledMatcher {
  const ordered = (items: readonly string[]): string[] =>
    [...new Set(items.map((item) => item.trim().toLowerCase()))]
      .filter((item) => item !== "")
      .sort(
        (left, right) => right.length - left.length || (left < right ? -1 : left > right ? 1 : 0),
      );
  const left = ordered(possessives);
  const right = ordered(nouns);
  if (left.length === 0 || right.length === 0) {
    return compile("(?!)", "u");
  }
  const whitespace = `[${whitespaceBody(tables)}]+`;
  const word = `[${wordBody(tables)}]`;
  const toBody = (items: string[]): string => items.map(escapeLiteral).join("|");
  return compile(
    `(?<!${word})(?:${toBody(left)})${whitespace}(?:${toBody(right)})(?!${word})`,
    "u",
  );
}
