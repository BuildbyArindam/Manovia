/**
 * The streak, said gently.
 *
 * The number is real and shown, because pretending it does not exist would be
 * dishonest — but it is never framed as something to protect. There is no
 * "don't break it", no countdown, no red, and no state in which the app tells
 * somebody they lost something. The rules behind the numbers (today stays open,
 * one missed day in any week is bridged, a gap is just a gap) are documented in
 * `app/services/mood/streak.py` and come from the server, so the words here never
 * have to guess what the numbers mean.
 *
 * The freeze is described as a fact about the rules ("you can miss one in any
 * week"), not as an asset that can be spent badly.
 */

import type { ReactElement } from "react";

import { MOOD_COPY, streakDetail, streakHeadline } from "../copy";
import type { MoodStreak } from "../types";

export interface StreakIndicatorProps {
  streak: MoodStreak | undefined;
  loading?: boolean;
}

export function StreakIndicator({ streak, loading = false }: StreakIndicatorProps): ReactElement {
  const headingId = "mood-streak-heading";
  const detail = streak === undefined ? null : streakDetail(streak);

  return (
    <section
      aria-labelledby={headingId}
      data-testid="mood-streak"
      data-state={streak?.state ?? "unknown"}
      className="rounded-2xl border border-border bg-surface p-6 shadow-soft"
    >
      <h2 id={headingId} className="text-lg">
        {MOOD_COPY.streakHeading}
      </h2>
      {loading || streak === undefined ? (
        <p className="mt-2 text-ink-muted">{MOOD_COPY.streakLoading}</p>
      ) : (
        <>
          <p className="mt-2" data-testid="mood-streak-headline">
            {streakHeadline(streak)}
          </p>
          {detail !== null ? (
            <p className="mt-1 text-sm text-ink-muted" data-testid="mood-streak-detail">
              {detail}
            </p>
          ) : null}
          {streak.timezone_fallback_used ? (
            <p className="mt-1 text-sm text-ink-muted">
              Days are being counted in UTC because this device did not report a timezone.
            </p>
          ) : null}
        </>
      )}
    </section>
  );
}
