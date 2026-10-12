/**
 * The suggested action: deterministic, tiny, optional, and never about the score.
 *
 * These are the sentences the app says back to somebody right after they have
 * said how they feel, so they are held to the same copy rules as the rest of the
 * surface — no cheerleading, no promises, no guilt — plus two of their own: the
 * suggestion may not quote the numbers back, and it must never be longer than a
 * couple of sentences (a "small action" that reads like a task list is not small).
 */
import { SUGGESTED_ACTIONS, fallbackAction, suggestedAction } from "./suggestedAction";
import type { MoodEntry } from "./types";

function entry(overrides: Partial<MoodEntry> = {}): MoodEntry {
  return {
    id: "entry-1",
    created_at: "2026-03-15T09:00:00Z",
    local_date: "2026-03-15",
    valence: 3,
    energy: 3,
    emotions: [],
    factors: [],
    note: null,
    timezone: "Asia/Kolkata",
    ...overrides,
  };
}

const CHEERLEADING = ["amazing", "awesome", "you got this", "great job", "stay positive"];
const GUILT = ["you should", "you must", "at least", "why didn't", "failed"];
const CLINICAL = ["diagnos", "medication", "mg", "disorder", "cure"];

describe("suggestedAction", () => {
  it("always returns exactly one of the reviewed sentences", () => {
    const reviewed = new Set<string>([...Object.values(SUGGESTED_ACTIONS), fallbackAction()]);

    for (let valence = 1; valence <= 5; valence += 1) {
      for (let energy = 1; energy <= 5; energy += 1) {
        for (const factors of [[], ["sleep"], ["work"], ["money"], ["friends"], ["weather"]]) {
          const action = suggestedAction(entry({ valence, energy, factors }));
          expect(reviewed.has(action)).toBe(true);
        }
      }
    }
  });

  it("keeps every sentence short enough to read in a breath", () => {
    for (const sentence of [...Object.values(SUGGESTED_ACTIONS), fallbackAction()]) {
      expect(sentence.length).toBeLessThanOrEqual(120);
      expect(sentence.split(". ").length).toBeLessThanOrEqual(2);
    }
  });

  it("passes the copy rules", () => {
    const all = [...Object.values(SUGGESTED_ACTIONS), fallbackAction()].join(" ").toLowerCase();

    for (const term of [...CHEERLEADING, ...GUILT, ...CLINICAL]) {
      expect(all).not.toContain(term);
    }
  });

  it("never quotes a mood number back", () => {
    for (let valence = 1; valence <= 5; valence += 1) {
      for (let energy = 1; energy <= 5; energy += 1) {
        const action = suggestedAction(entry({ valence, energy }));
        expect(action).not.toMatch(/your (mood|score|level|valence)/i);
        expect(action).not.toMatch(/\b[1-5](\/5)?\b/);
      }
    }
  });

  it("offers the smallest thing when somebody is at their lowest", () => {
    expect(suggestedAction(entry({ valence: 1, energy: 5 }))).toBe(SUGGESTED_ACTIONS.depleted);
    expect(suggestedAction(entry({ valence: 5, energy: 1 }))).toBe(SUGGESTED_ACTIONS.depleted);
  });

  it("responds to the context the person chose, not to a random rule", () => {
    expect(suggestedAction(entry({ valence: 2, energy: 4, factors: ["sleep"] }))).toBe(
      SUGGESTED_ACTIONS.sleep,
    );
    expect(suggestedAction(entry({ valence: 2, energy: 4, factors: ["friends"] }))).toBe(
      SUGGESTED_ACTIONS.people,
    );
    expect(suggestedAction(entry({ valence: 2, energy: 4, factors: ["money"] }))).toBe(
      SUGGESTED_ACTIONS.money,
    );
    expect(suggestedAction(entry({ valence: 2, energy: 4, factors: ["work"] }))).toBe(
      SUGGESTED_ACTIONS.work,
    );
  });

  it("notices fear and anxiety without naming them as a condition", () => {
    const action = suggestedAction(entry({ valence: 2, energy: 3, emotions: ["anxiety"] }));

    expect(action).toBe(SUGGESTED_ACTIONS.hardMoment);
    expect(action).not.toContain("anxiety");
  });

  it("is the same every time for the same check-in", () => {
    const one = suggestedAction(entry({ valence: 4, energy: 2 }));
    const two = suggestedAction(entry({ valence: 4, energy: 2 }));

    expect(one).toBe(two);
  });

  it("offers the ordinary, small thing on an ordinary day", () => {
    expect(suggestedAction(entry({ valence: 3, energy: 3 }))).toBe(SUGGESTED_ACTIONS.ordinary);
    expect(suggestedAction(entry({ valence: 3, energy: 2 }))).toBe(SUGGESTED_ACTIONS.quietMiddle);
    // An empty tank outranks a middling mood: the smallest offer wins.
    expect(suggestedAction(entry({ valence: 3, energy: 1 }))).toBe(SUGGESTED_ACTIONS.depleted);
  });

  it("does not turn a good day into homework", () => {
    expect(suggestedAction(entry({ valence: 5, energy: 5 }))).toBe(SUGGESTED_ACTIONS.goodEnergy);
    expect(suggestedAction(entry({ valence: 4, energy: 2 }))).toBe(SUGGESTED_ACTIONS.good);
  });
});
