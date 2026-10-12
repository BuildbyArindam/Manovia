/**
 * Every user-facing string the mood surface can show, in one file.
 *
 * AGENTS.md asks for copy that is "warm, plain, short, non-judgemental" and free
 * of toxic positivity. On this surface that is not a style preference: a streak
 * is the single most guilt-prone idea in the whole product, so the wording that
 * touches it is reviewable in one place, and `copy.test.ts` fails the build if a
 * guilt-inducing or cheerleading phrase creeps in.
 *
 * Rules the strings follow, so a future edit has something to check against:
 *
 * - **No punishment vocabulary.** Nothing is lost, broken, reset or missed. A gap
 *   is a gap.
 * - **No cheerleading.** "Amazing!" next to somebody's worst week is a lie. The
 *   confirmation is warm and plain: the check-in is saved, and that is enough.
 * - **No promises about outcomes.** An action is offered, not prescribed, and it
 *   is small enough to do in a minute. Nothing here is treatment.
 * - **Optional means optional.** Every suggestion is phrased so that not doing it
 *   costs nothing.
 *
 * The crisis copy is **not** here. It arrives pre-written from the backend
 * (`app/content/i18n/*.json`) and the UI never rewords it — see the CrisisCard,
 * which renders exactly the words it is handed.
 */

import { t } from "@/lib/i18n/i18n";

import type { MoodStreak } from "./types";

/** "1 day" / "3 days" without a plural library and without "1 day(s)". */
export function pluralDays(count: number): string {
  return count === 1 ? t("mood.pluralOneDay") : t("mood.pluralDays", { count });
}

export const MOOD_COPY = {
  get heading() {
    return t("mood.heading");
  },
  get lede() {
    return t("mood.lede");
  },
  get formHeading() {
    return t("mood.formHeading");
  },
  get formIntro() {
    return t("mood.formIntro");
  },
  get valenceLegend() {
    return t("mood.valenceLegend");
  },
  get valenceHint() {
    return t("mood.valenceHint");
  },
  get energyLegend() {
    return t("mood.energyLegend");
  },
  get energyHint() {
    return t("mood.energyHint");
  },
  get emotionsLegend() {
    return t("mood.emotionsLegend");
  },
  get emotionsHint() {
    return t("mood.emotionsHint");
  },
  get emotionsFull() {
    return t("mood.emotionsFull");
  },
  get factorsLegend() {
    return t("mood.factorsLegend");
  },
  get factorsHint() {
    return t("mood.factorsHint");
  },
  get factorsFull() {
    return t("mood.factorsFull");
  },
  get noteLegend() {
    return t("mood.noteLegend");
  },
  get notePlaceholder() {
    return t("mood.notePlaceholder");
  },
  get notePrivacy() {
    return t("mood.notePrivacy");
  },
  noteRemaining: (remaining: number): string => t("mood.noteRemaining", { count: remaining }),
  get noteOver() {
    return t("mood.noteOver");
  },
  get valenceRequired() {
    return t("mood.valenceRequired");
  },
  get energyRequired() {
    return t("mood.energyRequired");
  },
  get submit() {
    return t("mood.submit");
  },
  get submitting() {
    return t("mood.submitting");
  },
  get submitHint() {
    return t("mood.submitHint");
  },
  get errorHeading() {
    return t("mood.errorHeading");
  },
  get errorBody() {
    return t("mood.errorBody");
  },
  get errorConsent() {
    return t("mood.errorConsent");
  },
  get savedHeading() {
    return t("mood.savedHeading");
  },
  get savedBody() {
    return t("mood.savedBody");
  },
  get savedCrisisLine() {
    return t("mood.savedCrisisLine");
  },
  get savedStreakEmpty() {
    return t("mood.savedStreakEmpty");
  },
  /** The streak summary line: "{empty} · {days} in total." */
  savedStreakTotal: (totalCheckIns: number): string =>
    t("mood.savedStreakTotal", {
      empty: t("mood.savedStreakEmpty"),
      days: pluralDays(totalCheckIns),
    }),
  /** The heading the crisis card gets inside the saved-check-in screen. */
  get crisisSupportHeading() {
    return t("mood.crisisSupportHeading");
  },
  get savedAgain() {
    return t("mood.savedAgain");
  },
  get savedActionHeading() {
    return t("mood.savedActionHeading");
  },
  get streakHeading() {
    return t("mood.streakHeading");
  },
  get streakLoading() {
    return t("mood.streakLoading");
  },
  get reminderHeading() {
    return t("mood.reminderHeading");
  },
  get reminderBody() {
    return t("mood.reminderBody");
  },
  get reminderToggle() {
    return t("mood.reminderToggle");
  },
  get reminderTimeLabel() {
    return t("mood.reminderTimeLabel");
  },
  get reminderSaved() {
    return t("mood.reminderSaved");
  },
  get reminderTimeInvalid() {
    return t("mood.reminderTimeInvalid");
  },
  get historyHeading() {
    return t("mood.historyHeading");
  },
  get historyEmpty() {
    return t("mood.historyEmpty");
  },
  get historyLoading() {
    return t("mood.historyLoading");
  },
  get historyNoteLabel() {
    return t("mood.historyNoteLabel");
  },
  historyDelete: (day: string): string => t("mood.historyDelete", { day }),
  get historyDeleteConfirm() {
    return t("mood.historyDeleteConfirm");
  },
  get historyDeleteConfirmBody() {
    return t("mood.historyDeleteConfirmBody");
  },
  get historyDeleteYes() {
    return t("mood.historyDeleteYes");
  },
  get historyDeleteNo() {
    return t("mood.historyDeleteNo");
  },
  get historyDeleted() {
    return t("mood.historyDeleted");
  },
  get historyError() {
    return t("mood.historyError");
  },
  get crisisNotice() {
    return t("mood.crisisNotice");
  },
} as const;

/**
 * The streak line for the current state — the most delicate sentence in the app.
 *
 * Four states, four sentences, and none of them can say "you lost" or "don't
 * break it":
 *
 * - `never` — starting is day one, whenever that is.
 * - `today` — the check-in landed; the count is mentioned as a fact, not a target.
 * - `open` — a run is alive and today is unfinished, which is simply true.
 * - `returning` — welcome back, and the record of what they did before is still
 *   there.
 */
export function streakHeadline(streak: MoodStreak): string {
  switch (streak.state) {
    case "never":
      return t("mood.streak.never");
    case "today":
      return streak.current_streak === 1
        ? t("mood.streak.todayOne")
        : t("mood.streak.todayMany", { days: pluralDays(streak.current_streak) });
    case "open":
      return t("mood.streak.open", { days: pluralDays(streak.current_streak) });
    case "returning":
      return t("mood.streak.returning");
    default:
      return t("mood.streak.returning");
  }
}

/** The quieter second line: context, never a warning. */
export function streakDetail(streak: MoodStreak): string | null {
  if (streak.state === "returning") {
    const days = streak.days_since_last_check_in;
    const last =
      days === null
        ? t("mood.streak.lastAWhileAgo")
        : days === 1
          ? t("mood.streak.lastYesterday")
          : t("mood.streak.lastDaysAgo", { count: days });
    if (streak.longest_streak > 0) {
      return t("mood.streak.returningDetailLongest", {
        last,
        longest: pluralDays(streak.longest_streak),
      });
    }
    return t("mood.streak.returningDetail", { last });
  }
  if (streak.state === "open" && streak.freeze_available) {
    return t("mood.streak.openFreeze");
  }
  if (streak.state === "today" && streak.freeze_available && streak.freezes_used === 0) {
    return t("mood.streak.todayFreeze");
  }
  return null;
}

/**
 * A flat list of every string this feature can show, for the copy review.
 *
 * Used by `copy.test.ts` (guilt/cheerleading scan) and by
 * `scripts/verify_day15.py` (printed for a human read). Function-valued entries
 * are expanded with representative numbers so the review sees real sentences.
 */
export function allMoodStrings(): string[] {
  const strings: string[] = [];
  for (const value of Object.values(MOOD_COPY)) {
    if (typeof value === "string") {
      strings.push(value);
    }
  }
  strings.push(MOOD_COPY.noteRemaining(320), MOOD_COPY.historyDelete("11 March 2026"));
  for (const state of ["never", "today", "open", "returning"] as const) {
    for (const streak of [1, 3, 14]) {
      const sample: MoodStreak = {
        state,
        timezone: "Asia/Kolkata",
        timezone_fallback_used: false,
        today: "2026-03-15",
        current_streak: streak,
        longest_streak: streak + 2,
        checked_in_today: state === "today",
        last_check_in: "2026-03-15",
        days_since_last_check_in: state === "returning" ? 6 : 0,
        total_check_ins: streak + 4,
        freezes_used: 0,
        freeze_available: true,
      };
      strings.push(streakHeadline(sample));
      const detail = streakDetail(sample);
      if (detail !== null) {
        strings.push(detail);
      }
    }
  }
  return strings;
}
