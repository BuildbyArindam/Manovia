/**
 * Pseudo-localisation (Day 23): the string-overflow detector.
 *
 * Pseudo mode renders every English string transformed in three ways:
 *
 * 1. ASCII letters become accented look-alikes (``a → å``), so any string the
 *    reader can still read as plain English was NOT routed through the
 *    translation layer — a hard-coded string lights up instantly.
 * 2. The string grows by roughly 35%, because Hindi and Bengali routinely run
 *    longer than English and a container that fits English can still overflow.
 * 3. The result is wrapped in ⟦ ⟧ so a missing or doubled substitution is
 *    visible at a glance.
 *
 * Turn it on with ``?pseudo=1`` (the flag persists in localStorage) or from
 * the language switcher; it is a development aid and never a language a real
 * person is served.
 */

import type { PostProcessorModule } from "i18next";

const ACCENTS: Readonly<Record<string, string>> = {
  a: "å",
  b: "ƀ",
  c: "ç",
  d: "ð",
  e: "è",
  f: "ƒ",
  g: "ĝ",
  h: "ħ",
  i: "ï",
  j: "ĵ",
  k: "ĸ",
  l: "ļ",
  m: "ɱ",
  n: "ñ",
  o: "ô",
  p: "þ",
  q: "ǫ",
  r: "ŕ",
  s: "š",
  t: "ţ",
  u: "û",
  v: "ṽ",
  w: "ŵ",
  x: "ẋ",
  y: "ý",
  z: "ž",
  A: "Å",
  B: "Ɓ",
  C: "Ç",
  D: "Ð",
  E: "È",
  F: "Ƒ",
  G: "Ĝ",
  H: "Ħ",
  I: "Ï",
  J: "Ĵ",
  K: "Ķ",
  L: "Ļ",
  M: "Ṁ",
  N: "Ñ",
  O: "Ô",
  P: "Þ",
  Q: "Ǫ",
  R: "Ŕ",
  S: "Š",
  T: "Ţ",
  U: "Û",
  V: "Ṽ",
  W: "Ŵ",
  X: "Ẋ",
  Y: "Ý",
  Z: "Ž",
};

/** How much longer pseudo text runs than the English source. */
const EXPANSION_RATIO = 0.35;

/** One pseudo-glyph, repeated to pad a string to its expanded length. */
const PADDING_CHAR = "~";

export function pseudoLocalise(text: string): string {
  const accented = Array.from(text)
    .map((character) => ACCENTS[character] ?? character)
    .join("");
  const padLength = Math.ceil(text.length * EXPANSION_RATIO);
  return `⟦${accented}${PADDING_CHAR.repeat(padLength)}⟧`;
}

/**
 * The i18next hook: only the ``pseudo`` language is transformed, so turning
 * the mode on can never touch a real locale's copy. Interpolation has already
 * happened by the time a post-processor runs, which means an interpolated
 * value (a number, a name, a date) travels through the transformation too —
 * exactly what a layout check wants to see.
 */
export const pseudoPostProcessor: PostProcessorModule = {
  type: "postProcessor",
  name: "manoviaPseudo",
  process(value: string, _key: unknown, options: { lng?: string | string[] }): string {
    const languages = options.lng === undefined ? [] : Array.isArray(options.lng) ? options.lng : [options.lng];
    if (!languages.includes("pseudo")) {
      return value;
    }
    return pseudoLocalise(value);
  },
};
