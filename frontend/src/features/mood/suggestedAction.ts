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

import { t } from "@/lib/i18n/i18n";

import type { MoodEntry } from "./types";

/** The fallback: always available, always doable, always small. */
export function fallbackAction(): string {
  return t("mood.actionFallback");
}

/**
 * The suggestions, read through the translation layer at call time so a
 * language switch re-words them. The ids are the review vocabulary: each key
 * is one named situation from the docstring above.
 */
export const SUGGESTED_ACTIONS = {
  get depleted() {
    return t("mood.actions.depleted");
  },
  get sleep() {
    return t("mood.actions.sleep");
  },
  get people() {
    return t("mood.actions.people");
  },
  get work() {
    return t("mood.actions.work");
  },
  get money() {
    return t("mood.actions.money");
  },
  get weather() {
    return t("mood.actions.weather");
  },
  get hardMoment() {
    return t("mood.actions.hardMoment");
  },
  get quietMiddle() {
    return t("mood.actions.quietMiddle");
  },
  get ordinary() {
    return t("mood.actions.ordinary");
  },
  get goodEnergy() {
    return t("mood.actions.goodEnergy");
  },
  get good() {
    return t("mood.actions.good");
  },
};

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
    return fallbackAction();
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
