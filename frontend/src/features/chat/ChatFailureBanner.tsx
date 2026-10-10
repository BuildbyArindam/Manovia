/**
 * Errors and offline states, in plain words.
 *
 * Two separate surfaces because they mean different things:
 *
 * - **Offline** is a state of the device: a calm, always-true statement while
 *   the browser reports no network, with no blame and no spinner.
 * - **A failure** is about one thing that just happened: what went wrong, what
 *   it means for the message, and the one action that helps ("Try again",
 *   "Ask again", "Open Settings", "Start a new chat").
 *
 * Both are polite live regions rather than `role="alert"`: an assertive
 * interruption is the wrong register for somebody who has just written something
 * difficult, and the message is not time-critical.
 */

import type { ReactElement } from "react";

import type { ChatFailure } from "./chatState";
import { CHAT_ERRORS } from "./copy";

export interface ChatFailureBannerProps {
  failure: ChatFailure;
  onAction: () => void;
  onDismiss: () => void;
}

export function ChatFailureBanner({
  failure,
  onAction,
  onDismiss,
}: ChatFailureBannerProps): ReactElement {
  return (
    <div
      role="status"
      data-testid="chat-error"
      data-failure-kind={failure.kind}
      className="rounded-2xl border-2 border-border-strong bg-surface-muted p-4"
    >
      <p className="font-semibold">{failure.title}</p>
      <p className="mt-1 text-sm text-ink-muted">{failure.body}</p>
      {failure.requestId !== null && failure.requestId !== "unknown" ? (
        <p className="mt-1 text-xs text-ink-muted">Reference: {failure.requestId}</p>
      ) : null}
      <div className="mt-3 flex flex-wrap gap-2">
        <button
          type="button"
          data-testid="chat-error-action"
          onClick={onAction}
          className="inline-flex min-h-12 items-center rounded-xl bg-accent-bg px-4 py-2 font-semibold text-accent-fg"
        >
          {failure.action}
        </button>
        <button
          type="button"
          data-testid="chat-error-dismiss"
          onClick={onDismiss}
          className="inline-flex min-h-12 items-center rounded-xl border border-border-strong px-4 py-2 font-semibold"
        >
          Dismiss
        </button>
      </div>
    </div>
  );
}

/** The device-level notice, shown whenever the browser says there is no network. */
export function OfflineNotice(): ReactElement {
  return (
    <p
      role="status"
      data-testid="offline-notice"
      className="rounded-2xl border border-border-strong bg-surface-muted p-3 text-sm text-ink-muted"
    >
      <span className="font-semibold text-ink">{CHAT_ERRORS.offline.title}. </span>
      {CHAT_ERRORS.offline.body}
    </p>
  );
}
