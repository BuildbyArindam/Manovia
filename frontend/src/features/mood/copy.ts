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

import type { MoodStreak } from "./types";

/** "1 day" / "3 days" without a plural library and without "1 day(s)". */
export function pluralDays(count: number): string {
  return count === 1 ? "1 day" : `${count} days`;
}

export const MOOD_COPY = {
  heading: "Mood",
  lede: "A ten-second check-in. Two questions with words you would actually use, and a note if you want one.",

  formHeading: "How are you right now?",
  formIntro: "There is no wrong answer, and you can change any of it before you save.",

  valenceLegend: "How do you feel?",
  valenceHint: "Pick the closest face. Nothing is measured by it.",

  energyLegend: "How much energy do you have?",
  energyHint: "Move the slider with the arrow keys. The words change with it.",

  emotionsLegend: "What is here? (optional)",
  emotionsHint: "Up to three words. Skip it if none of them fit.",
  emotionsFull: "That is three. Tap one off to choose a different one.",

  factorsLegend: "What is going on around it? (optional)",
  factorsHint: "Up to five. These are context, not causes.",
  factorsFull: "That is five. Tap one off to choose a different one.",

  noteLegend: "Anything you want to add? (optional)",
  notePlaceholder: "A line, a sentence, a fragment. Or nothing at all.",
  notePrivacy: "Stored encrypted, and only ever shown back to you here.",
  noteRemaining: (remaining: number): string => `${remaining} characters left`,
  noteOver: "That is longer than the note can be. Try shortening it a little.",

  valenceRequired: "Choose the face that is closest. If none of them fit, pick the one you are nearest to.",
  energyRequired: "Choose how much energy you have.",

  submit: "Save check-in",
  submitting: "Saving…",
  submitHint: "Nothing is sent anywhere until you press Save check-in.",

  errorHeading: "That did not save.",
  errorBody:
    "Your check-in is still on this screen — nothing was lost. Press Save check-in to try again.",
  errorConsent: "This account still needs the data agreements before a check-in can be saved.",

  savedHeading: "Saved.",
  savedBody: "Thanks for telling me. That is the whole check-in.",
  savedCrisisLine: "Your check-in is saved. You can also look at this:",
  savedStreakEmpty: "Whenever you come back is fine.",
  savedAgain: "Add another check-in",
  savedActionHeading: "One small thing, if it helps",

  streakHeading: "Your check-ins",
  streakLoading: "Checking your last few days…",

  reminderHeading: "A daily reminder",
  reminderBody:
    "A nudge at the time you choose, on this device. Reminders are not being sent yet — the setting is kept for when they are.",
  reminderToggle: "Remember to remind me",
  reminderTimeLabel: "Remind me at",
  reminderSaved: "Saved on this device. No reminders are sent yet.",
  reminderTimeInvalid: "Use a time like 20:00.",

  historyHeading: "Recent check-ins",
  historyEmpty: "Nothing here yet. The first one takes ten seconds.",
  historyLoading: "Loading your check-ins…",
  historyNoteLabel: "Note",
  historyDelete: (day: string): string => `Delete the check-in from ${day}`,
  historyDeleteConfirm: "Delete it?",
  historyDeleteConfirmBody: "This removes the check-in and its note. It cannot be undone.",
  historyDeleteYes: "Yes, delete it",
  historyDeleteNo: "Keep it",
  historyDeleted: "Deleted. Your other check-ins are untouched.",
  historyError: "Your check-ins could not be loaded just now.",

  crisisNotice:
    "Manovia is not a crisis service. If you are in immediate danger, contact your local emergency number.",
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
      return "No streak running. Whenever you start, that is day one.";
    case "today":
      return streak.current_streak === 1
        ? "Today's check-in is saved — that is one day."
        : `Today's check-in is saved. That is ${pluralDays(streak.current_streak)} running.`;
    case "open":
      return `You have checked in for ${pluralDays(streak.current_streak)} in a row. Today is still open.`;
    case "returning":
      return "Welcome back.";
    default:
      return "Welcome back.";
  }
}

/** The quieter second line: context, never a warning. */
export function streakDetail(streak: MoodStreak): string | null {
  if (streak.state === "returning") {
    const days = streak.days_since_last_check_in;
    const last = days === null ? "a while ago" : days === 1 ? "yesterday" : `${days} days ago`;
    if (streak.longest_streak > 0) {
      return `Last check-in was ${last}. Your longest run so far is ${pluralDays(
        streak.longest_streak,
      )}, and starting again counts the same as starting the first time.`;
    }
    return `Last check-in was ${last}. Starting again counts the same as starting the first time.`;
  }
  if (streak.state === "open" && streak.freeze_available) {
    return "Miss a day and nothing resets — you can miss one in any week.";
  }
  if (streak.state === "today" && streak.freeze_available && streak.freezes_used === 0) {
    return "A missed day will not reset this.";
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
