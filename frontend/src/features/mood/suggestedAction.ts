/**
 * The one small thing offered after a check-in.
 *
 * Deterministic on purpose. There is no model in this path (the mood feature
 * does not talk to an LLM at all), so the same check-in always gets the same
 * suggestion — which is what makes the whole set reviewable in a diff, and what
 * lets a clinician read every sentence the app can say here in a few minutes.
 *
 * What a suggestion may be:
 *
 * - **tiny** — a minute or less, something with an obvious end;
 * - **optional** — phrased so that skipping it costs nothing;
 * - **concrete** — no "try to be kinder to yourself" advice, which is a task,
 *   not an action;
 * - **safe** — nothing about medication, nothing diagnostic, no promise that it
 *   will fix anything, and no exercise-as-treatment claim.
 *
 * What it may never be: a comment on the person's mood score. The numbers are
 * never quoted back; the suggestion responds to context the person chose to
 * share, or to nothing at all.
 */

import type { MoodEntry } from "./types";

/** The fallback: always available, always doable, always small. */
export const FALLBACK_ACTION =
  "Two slow breaths, with the out-breath longer than the in. That's the whole task.";

export const SUGGESTED_ACTIONS = {
  depleted: "Pick the next smallest thing and let the rest wait. A glass of water counts.",
  sleep: "Ten minutes lying down somewhere quiet. Resting counts, even if you do not sleep.",
  people: "One short message to somebody who knows you. It does not have to explain anything.",
  work: "Write down the first line of the thing that is pressing, then stop for now.",
  money: "Write the number down where you can see it. You do not have to solve it this minute.",
  weather: "A minute at a window, or just outside the door, if you can get there.",
  hardMoment: "Put a hand on the table and feel it for a few seconds. Nothing else needs doing.",
  quietMiddle: "Choose one thing and do half of it. Half counts.",
  ordinary: "Name one small thing that went okay today. It can be very small.",
  goodEnergy: "If you have energy to spare, spend ten minutes on something you actually like.",
  good: "Take ten seconds to let this land before you move on.",
} as const;

/**
 * Choose one suggestion. The order is the priority order, and it is deliberate:
 * the smallest offer wins, so somebody at their lowest is never handed the
 * biggest task.
 */
export function suggestedAction(
  entry: Pick<MoodEntry, "valence" | "energy" | "emotions" | "factors">,
): string {
  const { valence, energy, emotions, factors } = entry;
  const has = (...ids: string[]): boolean => ids.some((id) => factors.includes(id));

  if (valence <= 1 || energy <= 1) {
    return SUGGESTED_ACTIONS.depleted;
  }
  if (has("sleep") && (energy <= 3 || valence <= 2)) {
    // Sleep is offered when energy is low *or* when today already feels hard:
    // a bad night is a reason to rest, not a character flaw to push through.
    return SUGGESTED_ACTIONS.sleep;
  }
  if (has("friends", "family") && valence <= 2) {
    return SUGGESTED_ACTIONS.people;
  }
  if (has("work", "study") && valence <= 2) {
    return SUGGESTED_ACTIONS.work;
  }
  if (has("money") && valence <= 2) {
    return SUGGESTED_ACTIONS.money;
  }
  if (has("weather") && valence <= 2) {
    return SUGGESTED_ACTIONS.weather;
  }
  if (valence <= 2 && (emotions.includes("anxiety") || emotions.includes("fear"))) {
    return SUGGESTED_ACTIONS.hardMoment;
  }
  if (valence <= 2) {
    return FALLBACK_ACTION;
  }
  if (valence === 3 && energy <= 2) {
    return SUGGESTED_ACTIONS.quietMiddle;
  }
  if (valence === 3) {
    return SUGGESTED_ACTIONS.ordinary;
  }
  if (energy >= 4) {
    return SUGGESTED_ACTIONS.goodEnergy;
  }
  return SUGGESTED_ACTIONS.good;
}
