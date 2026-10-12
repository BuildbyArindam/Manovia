/**
 * What somebody sees after checking in with no connection (Day 25).
 *
 * The same promise as the online confirmation — it is saved — told truthfully:
 * saved *on this device*, sent by itself later, not in the history yet. And,
 * because the note cannot be screened until it reaches the server, the way to
 * the helplines is right here, every time, whatever the note says: the app
 * cannot know offline whether somebody needs it, so it never assumes they
 * don't. The helplines themselves work offline (the service worker keeps them).
 */

import { useEffect, useRef, type ReactElement } from "react";

import { CrisisHelpButton } from "@/components/CrisisHelpButton";
import { t } from "@/lib/i18n/i18n";

export interface MoodQueuedConfirmationProps {
  onAnother: () => void;
}

export function MoodQueuedConfirmation({ onAnother }: MoodQueuedConfirmationProps): ReactElement {
  const headingRef = useRef<HTMLHeadingElement | null>(null);

  useEffect(() => {
    headingRef.current?.focus();
  }, []);

  return (
    <section
      role="status"
      aria-labelledby="mood-queued-heading"
      data-testid="mood-queued-confirmation"
      className="space-y-4 rounded-2xl border border-border bg-surface p-6 shadow-soft"
    >
      <h2 id="mood-queued-heading" ref={headingRef} tabIndex={-1} className="text-lg">
        {t("mood.queuedHeading")}
      </h2>
      <p>{t("mood.queuedBody")}</p>
      <p className="text-sm text-ink-muted">{t("mood.queuedPrivacy")}</p>
      <div className="rounded-xl bg-surface-muted p-4">
        <p>{t("mood.queuedHelp")}</p>
        <div className="mt-3">
          <CrisisHelpButton />
        </div>
      </div>
      <button
        type="button"
        onClick={onAnother}
        className="inline-flex min-h-12 items-center justify-center rounded-xl border border-border px-6 text-base font-semibold"
      >
        {t("mood.savedAgain")}
      </button>
    </section>
  );
}
