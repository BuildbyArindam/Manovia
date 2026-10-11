/**
 * What somebody sees after saving a check-in.
 *
 * Three parts, in this order, because the order is the priority:
 *
 * 1. **It is saved.** One plain sentence, and the fact that it is theirs.
 * 2. **Help, if the note asked for it.** When the backend found HIGH risk in the
 *    note it returns the pre-written crisis message and the region's helplines;
 *    this renders them unaltered through the same CrisisCard the chat uses. The
 *    card sits *below* the saved line on purpose: the check-in was accepted, and
 *    nothing about needing help makes it less saved.
 * 3. **One small thing, if it helps.** Deterministic, tiny, optional.
 *
 * The heading receives focus when the card appears, so a keyboard user who
 * pressed Enter lands on the confirmation instead of being dropped back at the
 * top of a long page, and a screen reader announces it through `role="status"`.
 */

import { useEffect, useRef, type ReactElement } from "react";

import { CrisisCard } from "@/components/CrisisCard";
import { MOOD_COPY, pluralDays, streakDetail, streakHeadline } from "../copy";
import { suggestedAction } from "../suggestedAction";
import type { MoodCheckInResponse } from "../types";

export interface MoodConfirmationProps {
  result: MoodCheckInResponse;
  onAnother: () => void;
}

export function MoodConfirmation({ result, onAnother }: MoodConfirmationProps): ReactElement {
  const { entry, streak, crisis } = result;
  const headingRef = useRef<HTMLHeadingElement | null>(null);
  const action = suggestedAction(entry);
  const detail = streakDetail(streak);

  useEffect(() => {
    headingRef.current?.focus();
  }, []);

  return (
    <section
      role="status"
      aria-labelledby="mood-saved-heading"
      data-testid="mood-confirmation"
      className="space-y-5 rounded-2xl border border-border bg-surface p-6 shadow-soft"
    >
      <div>
        <h2 id="mood-saved-heading" ref={headingRef} tabIndex={-1} className="text-lg">
          {MOOD_COPY.savedHeading}
        </h2>
        <p className="mt-2">{crisis === null ? MOOD_COPY.savedBody : MOOD_COPY.savedCrisisLine}</p>
      </div>

      <div className="rounded-xl bg-surface-muted p-4" data-testid="mood-confirmation-streak">
        <p>{streakHeadline(streak)}</p>
        {detail !== null ? <p className="mt-1 text-sm text-ink-muted">{detail}</p> : null}
        <p className="mt-1 text-sm text-ink-muted">
          {MOOD_COPY.savedStreakEmpty} · {pluralDays(streak.total_check_ins)} in total.
        </p>
      </div>

      {crisis !== null ? (
        <div data-testid="mood-crisis">
          <CrisisCard
            message={crisis.message}
            resources={crisis.resources}
            emergency={crisis.emergency}
            announce
            heading="There is support for this, right now"
            testId="mood-crisis-card"
          />
          <p className="mt-2 text-sm text-ink-muted">{MOOD_COPY.crisisNotice}</p>
        </div>
      ) : null}

      <div data-testid="mood-suggested-action" className="rounded-xl border border-border p-4">
        <h3 className="text-base">{MOOD_COPY.savedActionHeading}</h3>
        <p className="mt-2">{action}</p>
      </div>

      <button
        type="button"
        onClick={onAnother}
        className="inline-flex min-h-12 items-center justify-center rounded-xl border border-border px-6 text-base font-semibold"
      >
        {MOOD_COPY.savedAgain}
      </button>
    </section>
  );
}
