/**
 * The copy rulebook, as a test.
 *
 * A mood streak is the most guilt-prone thing in a wellbeing app, and "no toxic
 * positivity" is an AGENTS.md requirement — so both are checked mechanically
 * against every string the feature can render, rather than trusted to review.
 *
 * The blocklists live here, not in `copy.ts`: the person writing copy should not
 * be the person relaxing the rule. If a future string needs to contain one of
 * these words, this test has to be argued with in a diff, which is exactly the
 * conversation that should happen.
 */
import { MOOD_COPY, allMoodStrings, pluralDays, streakDetail, streakHeadline } from "./copy";
import type { MoodStreak } from "./types";

/** Words and phrases that would make a missed day feel like a failure. */
const GUILT_TERMS = [
  "broken",
  "broke your",
  "you lost",
  "lost your streak",
  "you missed",
  "you failed",
  "failure",
  "don't break",
  "dont break",
  "keep it up",
  "no excuses",
  "should have",
  "streak over",
  "back to zero",
  "start over from zero",
  "sorry",
  "oops",
  "guilt",
  "shame",
  "shameful",
  "lazy",
  "try harder",
  "be consistent",
  "disappointed",
  "fell off",
  "slipped up",
  "last chance",
];

/** Cheerleading that would ring false on somebody's worst day. */
const TOXIC_POSITIVITY_TERMS = [
  "amazing",
  "awesome",
  "good vibes",
  "stay positive",
  "positive vibes",
  "everything happens for a reason",
  "you got this",
  "you've got this",
  "crush it",
  "smash it",
  "level up",
  "so proud",
  "well done",
  "great job",
  "congratulations",
  "turn that frown",
  "happiness is a choice",
  "just smile",
  "beat this",
];

/** Promise/claims the app must never make on this surface. */
const FORBIDDEN_CLAIMS = [
  "will cure",
  "cures",
  "cure your",
  "diagnos",
  "medication",
  "prescription",
  "mg",
  "disorder",
  "therapy is",
  "you are depressed",
  "you have depression",
  "will fix",
  "guaranteed",
  "proven to",
];

function findings(strings: string[], terms: string[]): string[] {
  const found: string[] = [];
  for (const text of strings) {
    const haystack = text.toLowerCase();
    for (const term of terms) {
      if (haystack.includes(term)) {
        found.push(`${term} → ${text}`);
      }
    }
  }
  return found;
}

function sampleStreak(overrides: Partial<MoodStreak> = {}): MoodStreak {
  return {
    state: "today",
    timezone: "Asia/Kolkata",
    timezone_fallback_used: false,
    today: "2026-03-15",
    current_streak: 3,
    longest_streak: 5,
    checked_in_today: true,
    last_check_in: "2026-03-15",
    days_since_last_check_in: 0,
    total_check_ins: 9,
    freezes_used: 0,
    freeze_available: true,
    ...overrides,
  };
}

describe("mood copy", () => {
  it("has strings to check at all", () => {
    expect(allMoodStrings().length).toBeGreaterThan(20);
  });

  it("contains no guilt-inducing wording", () => {
    expect(findings(allMoodStrings(), GUILT_TERMS)).toEqual([]);
  });

  it("contains no toxic positivity", () => {
    expect(findings(allMoodStrings(), TOXIC_POSITIVITY_TERMS)).toEqual([]);
  });

  it("makes no clinical promises or claims", () => {
    expect(findings(allMoodStrings(), FORBIDDEN_CLAIMS)).toEqual([]);
  });

  it("says out loud that reminders are not sent yet", () => {
    expect(MOOD_COPY.reminderBody).toContain("not being sent yet");
    expect(MOOD_COPY.reminderSaved).toContain("No reminders are sent");
  });

  it("keeps the note's privacy promise plain", () => {
    expect(MOOD_COPY.notePrivacy).toBe("Stored encrypted, and only ever shown back to you here.");
  });
});

describe("streak wording", () => {
  it("never makes a gap sound like a loss", () => {
    const texts = [
      streakHeadline(sampleStreak({ state: "returning", current_streak: 0, days_since_last_check_in: 6 })),
      streakDetail(sampleStreak({ state: "returning", current_streak: 0, days_since_last_check_in: 6 })),
      streakHeadline(sampleStreak({ state: "never", current_streak: 0, total_check_ins: 0 })),
    ].filter((text): text is string => text !== null);

    expect(findings(texts, GUILT_TERMS)).toEqual([]);
    expect(texts.join(" ")).toContain("Welcome back");
  });

  it("says welcome back, and nothing about losing ground", () => {
    const headline = streakHeadline(
      sampleStreak({ state: "returning", current_streak: 0, days_since_last_check_in: 6 }),
    );

    expect(headline).toBe("Welcome back.");
  });

  it("describes the freeze as a rule, not as a resource to protect", () => {
    const detail = streakDetail(sampleStreak({ state: "open", current_streak: 4 }));

    expect(detail).toContain("nothing resets");
    expect(findings([detail ?? ""], GUILT_TERMS)).toEqual([]);
  });

  it("does not count today as missed when it is still open", () => {
    const headline = streakHeadline(sampleStreak({ state: "open", current_streak: 4 }));

    expect(headline).toContain("still open");
    expect(headline).not.toContain("missed");
  });

  it("thanks somebody for telling it, rather than celebrating them", () => {
    expect(MOOD_COPY.savedBody).toContain("Thanks for telling me");
    expect(findings([MOOD_COPY.savedBody], TOXIC_POSITIVITY_TERMS)).toEqual([]);
  });

  it("pluralises days without a library and without `1 days`", () => {
    expect(pluralDays(1)).toBe("1 day");
    expect(pluralDays(0)).toBe("0 days");
    expect(pluralDays(14)).toBe("14 days");
  });
});
