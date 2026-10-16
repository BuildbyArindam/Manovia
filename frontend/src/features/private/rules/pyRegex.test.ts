/**
 * The Python-regex translator, held to Python's meaning.
 *
 * These are the cases where a plausible-looking translation is wrong in a way
 * that shows up as a *missed* risk rather than an error: a quantifier turned into
 * a literal, a word boundary that only knows ASCII, a whitespace class that
 * disagrees about U+001C. Each one is asserted against the behaviour the backend
 * engine has, and the first test exists because the translator got it wrong once
 * and the parity corpus caught it (see its comment).
 */

import { describe, expect, it } from "vitest";

import { readBundle } from "../testSupport/repoArtifacts";
import {
  PatternTranslationError,
  collapseRepeats,
  compileMarkerPattern,
  compilePhrase,
  compileRelationPattern,
  compileTranslated,
  escapeLiteral,
  isAscii,
  translatePythonRegex,
} from "./pyRegex";

const bundle = readBundle();
const tables = bundle.unicode;

describe("translatePythonRegex", () => {
  it("keeps a {n,m} quantifier as a quantifier (Day 41 regression)", () => {
    // `bn.jibon_sesh` is `\b(?:jibon\s+(?:ta\s+)?(?:\w+\s+){0,2}(?:sesh|shesh)…)\b`.
    // An earlier draft escaped every brace, because a *literal* brace has to be
    // escaped in a `u`-flag pattern; that turned `{0,2}` into the four characters
    // `{0,2}`, so the Bengali-romanised rule stopped matching
    // "jibon ta ekhanei shesh korbo" and the message read as NONE. The parity
    // corpus caught it; this test makes sure it cannot come back.
    const source = "\\b(?:jibon\\s+(?:ta\\s+)?(?:\\w+\\s+){0,2}(?:sesh|shesh))\\b";
    const translated = translatePythonRegex(source, tables);
    expect(translated).toContain("{0,2}");
    expect(translated).not.toContain("\\{");
    const matcher = compileTranslated(source, tables);
    expect(matcher.test("ami ar porte parchi na ei jibon ta ekhanei shesh korbo")).toBe(true);
    expect(matcher.test("jibon shesh")).toBe(true);
    expect(matcher.test("jibon ta shesh")).toBe(true);
    // More than two words in between is outside the quantifier's reach.
    expect(matcher.test("jibon ta a b c d shesh")).toBe(false);
  });

  it("escapes a literal brace, which a unicode-mode pattern would refuse", () => {
    const matcher = compileTranslated("a{b}c", tables);
    expect(matcher.test("a{b}c")).toBe(true);
    expect(translatePythonRegex("a{b}c", tables)).toContain("\\{");
  });

  it("supports the other quantifier spellings", () => {
    for (const source of ["a{2}", "a{2,}", "a{2,4}", "(?:ab){1,3}c"]) {
      expect(() => new RegExp(translatePythonRegex(source, tables), "u")).not.toThrow();
    }
    expect(compileTranslated("a{2,4}", tables).test("aaa")).toBe(true);
    expect(compileTranslated("a{2,4}", tables).test("a")).toBe(false);
  });

  it("makes \\w, \\d and \\s mean what Python means", () => {
    const word = compileTranslated("^\\w$", tables);
    expect(word.test("a")).toBe(true);
    expect(word.test("_")).toBe(true);
    expect(word.test("क")).toBe(true); // Devanagari letter: Python says word
    expect(word.test("০")).toBe(true); // Bengali digit: Python says word
    expect(word.test("!")).toBe(false);

    const digit = compileTranslated("^\\d$", tables);
    expect(digit.test("7")).toBe(true);
    expect(digit.test("৭")).toBe(true); // Bengali seven
    expect(digit.test("²")).toBe(false); // superscript two is No, not Nd

    const space = compileTranslated("^\\s$", tables);
    expect(space.test(" ")).toBe(true);
    expect(space.test("\u001c")).toBe(true); // Python's \s; JavaScript's is not
    expect(space.test("\u0085")).toBe(true);
    expect(space.test("\ufeff")).toBe(false); // JavaScript's \s; Python's is not
    expect(space.test("\u200b")).toBe(false); // zero-width space is neither
  });

  it("expands \\b to the boundary Python's \\w defines", () => {
    const matcher = compileTranslated("\\bdie\\b", tables);
    expect(matcher.test("i want to die")).toBe(true);
    expect(matcher.test("die!")).toBe(true);
    expect(matcher.test("dying")).toBe(false);
    expect(matcher.test("suicide")).toBe(false);
    // A Devanagari letter is a word character in Python, so there is no boundary
    // between it and "die" — an ASCII-only \b would wrongly match here.
    expect(matcher.test("कdie")).toBe(false);
    expect(matcher.test("क die")).toBe(true);
  });

  it("expands \\B as the complement", () => {
    const matcher = compileTranslated("\\Bdie", tables);
    // "studied": both sides of the position are word characters, so it is not a
    // boundary and \B matches. "i want to die" *is* a boundary, so it does not.
    expect(matcher.test("studied")).toBe(true);
    expect(matcher.test("i want to die")).toBe(false);
    // Start of string against a word character is a boundary too.
    expect(matcher.test("died")).toBe(false);
    expect(compileTranslated("\\bdie\\b", tables).test("i want to die")).toBe(true);
  });

  it("keeps lookarounds, groups and alternation", () => {
    expect(compileTranslated("(?<![\\w])kms(?![\\w])", tables).test("honestly kms")).toBe(true);
    expect(compileTranslated("(?<![\\w])kms(?![\\w])", tables).test("kmsx")).toBe(false);
    expect(compileTranslated("(?:a|b)c", tables).test("bc")).toBe(true);
    expect(compileTranslated("(a)\\1", tables).test("aa")).toBe(true);
  });

  it("translates Python's named-group spelling", () => {
    expect(translatePythonRegex("(?P<word>\\w+)-\\1", tables)).toContain("(?<word>");
    expect(translatePythonRegex("(?P<word>\\w+)-(?P=word)", tables)).toContain("\\k<word>");
  });

  it("unescapes punctuation Python escapes and a unicode pattern refuses", () => {
    expect(compileTranslated("a\\,b", tables).test("a,b")).toBe(true);
    expect(compileTranslated("a\\ b", tables).test("a b")).toBe(true);
  });

  it("refuses what it cannot translate, instead of never matching", () => {
    // Refused by the translator itself.
    for (const source of ["[\\W]", "[\\D]", "[\\S]", "a[", "[]]", "a\\"]) {
      expect(() => translatePythonRegex(source, tables), source).toThrow(PatternTranslationError);
    }
    // Translated, then refused by the JavaScript compiler — same error type, so a
    // broken rule fails the bundle load with the pattern named in the message.
    expect(() => compileTranslated("\\q", tables)).toThrow(PatternTranslationError);
    expect(() => compileTranslated("(?P=word)", tables)).toThrow(PatternTranslationError);
  });

  it("leaves a literal [ inside a class escaped", () => {
    expect(compileTranslated("[a[b]", tables).test("[")).toBe(true);
  });
});

describe("compilePhrase", () => {
  it("matches an ASCII phrase on word boundaries with flexible spacing", () => {
    const { pattern } = compilePhrase("want to die", tables);
    expect(pattern.test("i want to die")).toBe(true);
    expect(pattern.test("i   want\tto  die")).toBe(true);
    expect(pattern.test("i dont want to die tonight")).toBe(true);
    expect(pattern.test("wanted to diet")).toBe(false);
  });

  it("also matches the phrase with its spaces removed", () => {
    const { squashed } = compilePhrase("want to die", tables);
    expect(squashed).not.toBeNull();
    expect((squashed as NonNullable<typeof squashed>).test("iwanttodienow")).toBe(true);
  });

  it("matches a phrase with repeated letters collapsed on both sides", () => {
    const { collapsed } = compilePhrase("kill myself", tables);
    expect(collapsed).not.toBeNull();
    // The projection collapses runs of identical letters, so "kill" becomes "kil"
    // in the pattern *and* in the text — the engine passes the collapsed text, not
    // the original, and that symmetry is what keeps the comparison honest.
    const stretched = "kiiiillll myyyyyself";
    const projection = collapseRepeats(tables, stretched);
    expect(projection).toBe("kil myself");
    expect((collapsed as NonNullable<typeof collapsed>).test(projection)).toBe(true);
    expect((collapsed as NonNullable<typeof collapsed>).test(stretched)).toBe(false);
  });

  it("treats a non-ASCII phrase as a substring, with no squashed projection", () => {
    const { pattern, squashed } = compilePhrase("জীবন শেষ", tables);
    expect(pattern.test("আমার জীবন শেষ হয়ে গেছে")).toBe(true);
    expect(squashed).toBeNull();
  });

  it("reports nothing collapsed when there is nothing to collapse", () => {
    expect(compilePhrase("die", tables).collapsed).toBeNull();
  });
});

describe("marker and relation patterns", () => {
  it("tries the longest marker first", () => {
    const matcher = compileMarkerPattern(["my", "my best friend", "friend"], tables);
    expect(matcher.test("my best friend said")).toBe(true);
    expect(matcher.test("my mum said")).toBe(true);
    expect(matcher.test("nobody said")).toBe(false);
    expect(matcher.test("myself")).toBe(false); // word boundaries, not substrings
    expect(matcher.source.indexOf("my best friend")).toBeLessThan(matcher.source.indexOf("|my)"));
  });

  it("never matches when the marker list is empty", () => {
    expect(compileMarkerPattern([], tables).test("anything at all")).toBe(false);
    expect(compileRelationPattern([], ["mum"], tables).test("my mum")).toBe(false);
  });

  it("joins possessives and relation nouns with flexible whitespace", () => {
    const matcher = compileRelationPattern(["my", "her"], ["mum", "best friend"], tables);
    expect(matcher.test("my mum said")).toBe(true);
    expect(matcher.test("her  best friend")).toBe(true);
    expect(matcher.test("my teacher")).toBe(false);
  });
});

describe("helpers", () => {
  it("collapses repeated word characters only", () => {
    expect(collapseRepeats(tables, "diiiie")).toBe("die");
    expect(collapseRepeats(tables, "a b")).toBe("a b");
    expect(collapseRepeats(tables, "kill")).toBe("kil");
  });

  it("escapes every JavaScript syntax character in a literal", () => {
    expect(escapeLiteral("a.b*c")).toBe("a\\.b\\*c");
    // `compilePhrase` takes the phrase as written and escapes it itself.
    expect(compilePhrase("(a)", tables).pattern.test("said (a) aloud")).toBe(true);
    expect(compilePhrase("(a)", tables).pattern.test("said a aloud")).toBe(false);
  });

  it("recognises ASCII the way str.isascii does", () => {
    expect(isAscii("want to die")).toBe(true);
    expect(isAscii("জীবন")).toBe(false);
    expect(isAscii("𝔄")).toBe(false);
  });
});
