/**
 * Dump every user-facing mood string as JSON, for `scripts/verify_day15.py`.
 *
 * The verification script must *see* the copy somebody reads, not a second copy
 * of it typed into Python: that is how a scan for guilt-inducing wording turns
 * into a scan of the wording a Python file once claimed the app used. So the
 * frontend hands its real strings over — `allMoodStrings()` plus the chip labels
 * and scale words — and the script does its own scanning and reporting.
 *
 * Run it from `frontend/` via esbuild (Vitest's `vite-node` is not installed):
 *
 *   npx esbuild scripts/mood-copy-dump.ts --bundle --format=esm --platform=node \
 *     --alias:@=./src --outfile=/tmp/mood-copy-dump.mjs && node /tmp/mood-copy-dump.mjs
 *
 * `scripts/verify_day15.py` does exactly that, so a human only ever runs the
 * Python script.
 */

import {
  MOOD_COPY,
  allMoodStrings,
  pluralDays,
  streakHeadline,
  streakDetail,
} from "@/features/mood/copy";
import {
  EMOTION_IDS,
  ENERGY_POINTS,
  FACTOR_IDS,
  MAX_EMOTIONS,
  MAX_FACTORS,
  MOOD_EMOTIONS,
  MOOD_FACTORS,
  NOTE_MAX_CHARS,
  SCALE_MAX,
  SCALE_MIN,
  VALENCE_POINTS,
} from "@/features/mood/taxonomy";

const payload = {
  strings: allMoodStrings(),
  // Also the pieces the strings are built out of, so a review sees the whole
  // vocabulary rather than only the sentences that happen to be complete today.
  extra: [
    ...MOOD_EMOTIONS.map((option) => option.label),
    ...MOOD_FACTORS.map((option) => option.label),
    ...VALENCE_POINTS.map((point) => point.label),
    ...ENERGY_POINTS.map((point) => point.label),
    pluralDays(1),
    pluralDays(2),
    pluralDays(14),
  ],
  constants: {
    heading: MOOD_COPY.heading,
    submit: MOOD_COPY.submit,
  },
  taxonomy: {
    emotions: [...EMOTION_IDS],
    factors: [...FACTOR_IDS],
    max_emotions: MAX_EMOTIONS,
    max_factors: MAX_FACTORS,
    note_max_chars: NOTE_MAX_CHARS,
    scale_min: SCALE_MIN,
    scale_max: SCALE_MAX,
  },
};

// Sample streak lines across every state, so the review reads real sentences
// rather than a function reference.
const streaks = (["never", "today", "open", "returning"] as const).flatMap((state) =>
  [1, 3, 14].flatMap((days) => {
    const streak = {
      state,
      timezone: "Asia/Kolkata",
      timezone_fallback_used: false,
      today: "2026-03-15",
      current_streak: days,
      longest_streak: days + 2,
      checked_in_today: state === "today",
      last_check_in: "2026-03-15",
      days_since_last_check_in: state === "returning" ? 6 : 0,
      total_check_ins: days + 4,
      freezes_used: 0,
      freeze_available: true,
    };
    const detail = streakDetail(streak);
    return [streakHeadline(streak), ...(detail === null ? [] : [detail])];
  }),
);

process.stdout.write(JSON.stringify({ ...payload, strings: [...payload.strings, ...streaks] }));
