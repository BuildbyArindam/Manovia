/**
 * Python string semantics, in a browser.
 *
 * The Private Mode rules engine is a port of `backend/app/services/safety`, and
 * the two must agree message for message (see `parity.test.ts`). Almost all of
 * the disagreement risk is in the small stuff: what counts as whitespace, what
 * `casefold()` does, whether a length is counted in code points or UTF-16 units.
 * Everything in this file exists so the rest of the port can be written the way
 * the Python reads.
 *
 * Each helper names the Python behaviour it reproduces:
 *
 * - {@link PY_WHITESPACE} is Python's `\s`, which is *not* JavaScript's: Python
 *   matches U+001C–U+001F and U+0085 and does not match U+FEFF.
 * - {@link casefold} is `str.casefold()`. JavaScript has no case folding, so the
 *   352 code points where `toLowerCase()` disagrees come from the bundle, which
 *   derives them from a Python-generated fixture
 *   (`backend/scripts/export_casefold_cases.py`).
 * - {@link codePointLength} and {@link sliceCodePoints} are `len()` and slicing,
 *   which count code points; JavaScript counts UTF-16 units. The engine's input
 *   cap and its long-message windowing both depend on that.
 * - {@link pySplit} and {@link pyStrip} are `str.split()` and `str.strip()`.
 */

import type { UnicodeTables } from "./bundleSchema";

/** A regular-expression escape for one code point (`\u{1f600}`, `\u0009`). */
export function escapeCodePoint(codePoint: number): string {
  return `\\u{${codePoint.toString(16)}}`;
}

/** A character-class body covering inclusive code-point ranges. */
export function rangesToClassBody(ranges: readonly (readonly [number, number])[]): string {
  return ranges
    .map(([low, high]) =>
      low === high ? escapeCodePoint(low) : `${escapeCodePoint(low)}-${escapeCodePoint(high)}`,
    )
    .join("");
}

/** The code points themselves, as a class body (no ranges). */
export function codePointsToClassBody(codePoints: readonly number[]): string {
  return [...codePoints]
    .sort((left, right) => left - right)
    .map(escapeCodePoint)
    .join("");
}

/**
 * Python's `\w` as a class body.
 *
 * Verified over every code point by
 * `backend/tests/safety/test_private_bundle.py::test_word_class_claim_holds_for_every_codepoint`.
 */
export function wordClassBody(tables: UnicodeTables): string {
  return tables.word_class_body;
}

/** Python's `\s`, as a compiled regular expression with the `u` flag. */
export function whitespaceRegex(tables: UnicodeTables, flags = "u"): RegExp {
  return new RegExp(`[${codePointsToClassBody(tables.whitespace_codepoints)}]`, flags);
}

/** Python's `\s+`. */
export function whitespaceRunRegex(tables: UnicodeTables): RegExp {
  return new RegExp(`[${codePointsToClassBody(tables.whitespace_codepoints)}]+`, "gu");
}

/** Python's `\S+` (`normalise._TOKEN`). */
export function nonWhitespaceRunRegex(tables: UnicodeTables): RegExp {
  return new RegExp(`[^${codePointsToClassBody(tables.whitespace_codepoints)}]+`, "gu");
}

/** True for exactly the characters Python's `str.isspace()` puts in `\s`. */
export function isPyWhitespace(tables: UnicodeTables, char: string): boolean {
  const codePoint = char.codePointAt(0);
  return codePoint !== undefined && tables.whitespace_codepoints.includes(codePoint);
}

/** Python's `str.split()` with no argument: split on whitespace runs, drop empties. */
export function pySplit(tables: UnicodeTables, text: string): string[] {
  if (text === "") {
    return [];
  }
  return text.split(whitespaceRunRegex(tables)).filter((token) => token !== "");
}

/**
 * Python's `str.strip(chars)`.
 *
 * With no argument it strips Python whitespace — *not* JavaScript's, which also
 * removes U+FEFF. With an argument it strips any of the given characters, which
 * is what `to_clause_text` needs for `"| "`.
 */
export function pyStrip(tables: UnicodeTables, text: string, chars?: string): string {
  const unwanted =
    chars === undefined
      ? new Set(tables.whitespace_codepoints.map((codePoint) => String.fromCodePoint(codePoint)))
      : new Set([...chars]);
  const units = [...text];
  let start = 0;
  let end = units.length;
  while (start < end) {
    const char = units[start];
    if (char === undefined || !unwanted.has(char)) {
      break;
    }
    start += 1;
  }
  while (end > start) {
    const char = units[end - 1];
    if (char === undefined || !unwanted.has(char)) {
      break;
    }
    end -= 1;
  }
  return units.slice(start, end).join("");
}

/**
 * Python's `len(text)`: code points, not UTF-16 units.
 *
 * Counted by skipping low surrogates rather than by spreading the string, because
 * this runs on every message and a 64k-character input would otherwise allocate a
 * 64k-element array to produce one number.
 */
export function codePointLength(text: string): number {
  let count = 0;
  for (let index = 0; index < text.length; index += 1) {
    const code = text.charCodeAt(index);
    if (code >= 0xdc00 && code <= 0xdfff) {
      continue; // the second half of a surrogate pair: same code point
    }
    count += 1;
  }
  return count;
}

/** Python's `text[start:stop]`, in code points. Negative start counts from the end. */
export function sliceCodePoints(text: string, start?: number, stop?: number): string {
  const units = [...text];
  const from = start === undefined ? 0 : start < 0 ? Math.max(0, units.length + start) : start;
  const to = stop === undefined ? units.length : stop < 0 ? Math.max(0, units.length + stop) : stop;
  return units.slice(from, to).join("");
}

/**
 * Python's `str.casefold()`.
 *
 * JavaScript's `toLowerCase()` is the starting point; the bundle's override table
 * carries every code point where the two disagree, in either direction — including
 * the characters Unicode 15–17 added after Python 3.11's tables were frozen, which
 * a browser would lowercase and Python leaves alone.
 */
export function makeCasefold(tables: UnicodeTables): (text: string) => string {
  const overrides = new Map<number, string>(
    Object.entries(tables.casefold_overrides).map(([key, value]) => [Number(key), value]),
  );
  return (text: string): string => {
    let out = "";
    for (const char of text) {
      const codePoint = char.codePointAt(0);
      const override = codePoint === undefined ? undefined : overrides.get(codePoint);
      out += override !== undefined ? override : char.toLowerCase();
    }
    return out;
  };
}

/** `unicodedata.normalize("NFKC", text)` followed by deleting the invisible characters. */
export function stripInvisible(tables: UnicodeTables, text: string): string {
  const invisible = new Set(
    tables.invisible_codepoints.map((codePoint) => String.fromCodePoint(codePoint)),
  );
  let out = "";
  for (const char of text.normalize("NFKC")) {
    if (!invisible.has(char)) {
      out += char;
    }
  }
  return out;
}

/** `normalise.strip_latin_combining`: NFKD, drop stacked Latin marks, NFKC again. */
export function stripLatinCombining(tables: UnicodeTables, text: string): string {
  const combining = new RegExp(`[${rangesToClassBody(tables.latin_combining_ranges)}]`, "gu");
  return text.normalize("NFKD").replace(combining, "").normalize("NFKC");
}

/** Python's `str.isalpha()`: the Unicode letter categories, and nothing else. */
export function isAlpha(char: string): boolean {
  return /^\p{L}$/u.test(char);
}
