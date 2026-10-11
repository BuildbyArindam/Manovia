/**
 * The copy is an artefact, so it is tested like one.
 *
 * Two failure modes are worth a test, and both were live risks on this surface:
 *
 * 1. **Causal language.** An association between a tag and a mood level is not a
 *    cause, and "poor sleep is making you low" is exactly the kind of sentence a
 *    dashboard drifts into. The words are banned here.
 * 2. **Toxic positivity.** A downward trend stated as an opportunity is a lie
 *    told to somebody having a bad month.
 *
 * The backend's own generated sentences (factor notes, trend notes, the weekly
 * summary) are checked too — they are what a person actually reads — which is why
 * this file imports the Python-side wording contract from a fixture rather than
 * trusting the two halves to agree.
 */

import { describe, expect, it } from "vitest";

import { INSIGHTS_COPY, allInsightsStrings, trendLabel } from "./copy";

/** Phrases that assert a cause. Any one of them in user-facing copy is a bug. */
const CAUSAL_PHRASES = [
  "causes",
  "caused",
  "causing",
  "because of",
  "due to",
  "leads to",
  "led to",
  "results in",
  "resulted in",
  "makes you",
  "made you",
  "will make you",
  "is why you",
  "is the reason",
  "responsible for your",
];

/**
 * Phrases that turn somebody's low week into an achievement problem.
 *
 * "at least" is deliberately narrowed to its minimising form ("at least you…",
 * "at least it…"). The bare phrase is legitimate here and load-bearing: every
 * minimum-data rule in this feature is *about* having at least N days, and a
 * blanket ban would force the checks to be written less clearly.
 */
const CHEERLEADING_PHRASES = [
  "amazing",
  "awesome",
  "you've got this",
  "stay positive",
  "look on the bright side",
  "at least you",
  "at least it",
  "at least there",
  "just remember",
  "only you can",
  "you should be proud",
  "great job",
  "well done",
  "congratulations",
];

/** Words that read as a verdict on the person rather than a description. */
const JUDGEMENTAL_WORDS = ["lazy", "failure", "weak", "broken", "fault", "excuse"];

function contains(haystack: string, needle: string): boolean {
  // Word-ish boundary: "at least" must not match inside "greatest", but
  // punctuation and spaces both count as boundaries.
  const pattern = new RegExp(
    `(^|[^a-z])${needle.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}([^a-z]|$)`,
    "i",
  );
  return pattern.test(haystack);
}

describe("insights copy", () => {
  const strings = allInsightsStrings();

  it("offers a reviewable list of every sentence the surface can show", () => {
    expect(strings.length).toBeGreaterThan(15);
    for (const value of strings) {
      expect(value.length).toBeGreaterThan(0);
      expect(value.trim()).toBe(value);
    }
  });

  it("never claims a cause", () => {
    const offenders: string[] = [];
    for (const value of strings) {
      for (const phrase of CAUSAL_PHRASES) {
        if (contains(value, phrase)) {
          offenders.push(`${phrase} -> ${value}`);
        }
      }
    }
    expect(offenders).toEqual([]);
  });

  it("never cheerleads", () => {
    const offenders: string[] = [];
    for (const value of strings) {
      for (const phrase of CHEERLEADING_PHRASES) {
        if (contains(value, phrase)) {
          offenders.push(`${phrase} -> ${value}`);
        }
      }
    }
    expect(offenders).toEqual([]);
  });

  it("never passes a verdict on the person", () => {
    const offenders: string[] = [];
    for (const value of strings) {
      for (const word of JUDGEMENTAL_WORDS) {
        if (contains(value, word)) {
          offenders.push(`${word} -> ${value}`);
        }
      }
    }
    expect(offenders).toEqual([]);
  });

  it("states the limitation, not just the finding", () => {
    const joined = strings.join(" ");
    // The association disclaimer, in the words the charts render.
    expect(joined).toContain("association, not a causal link");
    // The minimum-data rule has to be visible somewhere.
    expect(joined).toMatch(/at least 7 days/);
    expect(joined).toMatch(/at least 5 days per group|need \d+ per group/i);
  });

  it("promises nothing about the future", () => {
    const offenders = strings.filter(
      (value) =>
        /will feel|you will|guarantee|predict|predicts|prediction that/i.test(value) &&
        !/not predictions|descriptive patterns, not predictions/i.test(value),
    );
    expect(offenders).toEqual([]);
  });

  it("labels every trend state without a judgement", () => {
    expect(trendLabel("up")).toBe("Trending up");
    expect(trendLabel("down")).toBe("Trending down");
    expect(trendLabel("flat")).toBe("Stable");
    expect(trendLabel("not_enough_data")).toBe("Not enough data");
  });

  it("says 'not enough data' rather than implying something from nothing", () => {
    expect(INSIGHTS_COPY.rollingTrendUnknown).toMatch(/not enough data/i);
    expect(INSIGHTS_COPY.factorsNoData("sleep", 5)).toContain("Not enough days");
    expect(INSIGHTS_COPY.emptyBody).toMatch(/check in for a few days/i);
  });

  it("keeps the 7-day summary honest about its own sources", () => {
    // The weekly summary is a template, and the explainer has to say so.
    expect(INSIGHTS_COPY.aboutLimitations.join(" ")).toMatch(/notes are never used/i);
  });
});
