/**
 * The entry point to Private Mode, shown instead of the panel until the reader
 * asks for it.
 *
 * It is deliberately tiny: this component is on the /chat critical path, and the
 * day-25 bundle budget (25 KB gzip per route) is why the rest of Private Mode is
 * behind a dynamic import. No rules engine, no model manager, no crypto — a
 * heading, the one-sentence promise, and a button.
 *
 * It is a button and not a switch on purpose. Turning Private Mode on is a
 * consent step with a size, a capability verdict and a passphrase behind it, so
 * the reader gets the panel first and flips the switch there, where the
 * consequences are written down.
 */

import { useId, type ReactElement } from "react";

import { PRIVATE_COPY } from "./copy";

export interface PrivateModeEntryProps {
  onOpen: () => void;
}

export function PrivateModeEntry({ onOpen }: PrivateModeEntryProps): ReactElement {
  const headingId = useId();
  const ledeId = useId();

  return (
    <section
      aria-labelledby={headingId}
      data-testid="private-mode-entry"
      className="rounded-2xl border border-border bg-surface p-4 shadow-soft"
    >
      <h2 id={headingId} className="text-lg font-semibold">
        {PRIVATE_COPY.panelHeading}
      </h2>
      <p id={ledeId} className="mt-1 text-sm text-ink-muted">
        {PRIVATE_COPY.panelLede}
      </p>
      <button
        type="button"
        data-testid="private-mode-open"
        onClick={onOpen}
        aria-describedby={ledeId}
        className="mt-3 inline-flex min-h-12 items-center rounded-xl border border-border-strong px-4 py-2 font-semibold"
      >
        {PRIVATE_COPY.panelHeading}
      </button>
    </section>
  );
}
