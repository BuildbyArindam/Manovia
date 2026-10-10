/**
 * The chat controls: save, clear, start over, language.
 *
 * Each control is honest about what it does, because three of the four are
 * about data:
 *
 * - **"Save this conversation"** is the `store_chat` consent surfacing in the
 *   UI. It applies to the *next* session — a conversation already in memory
 *   cannot be retroactively written to the database, and the text under the
 *   switch says so instead of implying otherwise. Turning it on without the
 *   consent granted gets a `403 consent_required` from the backend, which the
 *   page turns into "one more agreement needed".
 * - **"Clear conversation"** empties the screen. It does not delete stored
 *   rows, so it must not look like it does; the confirmation says exactly that.
 * - **"Start a new chat"** drops the session (so the next message opens a fresh
 *   one) and empties the screen.
 * - **Language** is sent with each message as `locale`, which selects the
 *   language of the *pre-written* crisis and check-in copy. It does not change
 *   the model's language — the copy below the selector says that plainly rather
 *   than letting a person believe a switch can do something it cannot.
 */

import { useState, type ReactElement } from "react";

import { CHAT_COPY } from "./copy";
import { CHAT_LOCALES, LOCALE_LABELS, type ChatLocale } from "./types";

export interface ChatControlsProps {
  saveHistory: boolean;
  /** What the current session actually is, per the server. */
  sessionIsPersistent: boolean;
  hasSession: boolean;
  locale: ChatLocale;
  disabled?: boolean;
  onSaveHistoryChange: (value: boolean) => void;
  onClear: () => void;
  onNewChat: () => void;
  onLocaleChange: (value: ChatLocale) => void;
}

export function ChatControls({
  saveHistory,
  sessionIsPersistent,
  hasSession,
  locale,
  disabled = false,
  onSaveHistoryChange,
  onClear,
  onNewChat,
  onLocaleChange,
}: ChatControlsProps): ReactElement {
  const [confirmingClear, setConfirmingClear] = useState(false);

  return (
    <section
      aria-labelledby="chat-controls-heading"
      data-testid="chat-controls"
      className="rounded-2xl border border-border bg-surface p-4 shadow-soft"
    >
      <h2 id="chat-controls-heading" className="sr-only">
        Conversation controls
      </h2>

      <div className="flex flex-wrap items-center gap-3">
        <label className="inline-flex cursor-pointer items-start gap-3 rounded-xl border border-border-strong px-3 py-2">
          <input
            type="checkbox"
            role="switch"
            data-testid="save-toggle"
            checked={saveHistory}
            disabled={disabled}
            onChange={(event) => {
              onSaveHistoryChange(event.target.checked);
            }}
            className="mt-1 h-5 w-5 accent-accent-bg"
          />
          <span>
            <span className="block font-semibold">{CHAT_COPY.saveConversation}</span>
            <span className="block text-sm text-ink-muted">{CHAT_COPY.saveConversationHint}</span>
            {hasSession && saveHistory !== sessionIsPersistent ? (
              <span className="block text-sm text-ink-muted">
                Applies from your next chat — this one is already{" "}
                {sessionIsPersistent ? "being saved" : "memory only"}.
              </span>
            ) : null}
          </span>
        </label>

        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            data-testid="new-chat-button"
            onClick={onNewChat}
            className="inline-flex min-h-12 items-center rounded-xl border border-border-strong px-4 py-2 font-semibold"
          >
            {CHAT_COPY.newChat}
          </button>

          {confirmingClear ? (
            <span className="inline-flex flex-wrap items-center gap-2">
              <span className="text-sm text-ink-muted">{CHAT_COPY.clearConfirm}</span>
              <button
                type="button"
                data-testid="confirm-clear-button"
                onClick={() => {
                  setConfirmingClear(false);
                  onClear();
                }}
                className="inline-flex min-h-12 items-center rounded-xl bg-accent-bg px-4 py-2 font-semibold text-accent-fg"
              >
                Yes, clear it
              </button>
              <button
                type="button"
                onClick={() => {
                  setConfirmingClear(false);
                }}
                className="inline-flex min-h-12 items-center rounded-xl border border-border-strong px-4 py-2 font-semibold"
              >
                Keep it
              </button>
            </span>
          ) : (
            <button
              type="button"
              data-testid="clear-button"
              onClick={() => {
                setConfirmingClear(true);
              }}
              className="inline-flex min-h-12 items-center rounded-xl border border-border-strong px-4 py-2 font-semibold"
            >
              {CHAT_COPY.clearConversation}
            </button>
          )}
        </div>

        <label className="inline-flex items-center gap-2">
          <span className="font-semibold">{CHAT_COPY.languageLabel}</span>
          <select
            data-testid="language-select"
            value={locale}
            onChange={(event) => {
              const next = event.target.value as ChatLocale;
              if (CHAT_LOCALES.includes(next)) {
                onLocaleChange(next);
              }
            }}
            className="min-h-12 rounded-xl border border-border-strong bg-surface px-3 py-2"
          >
            {CHAT_LOCALES.map((code) => (
              <option key={code} value={code}>
                {LOCALE_LABELS[code]}
              </option>
            ))}
          </select>
        </label>
      </div>

      <p className="mt-3 text-sm text-ink-muted">
        Language changes the written crisis and check-in messages. Manovia replies in the language
        you write in.
      </p>
    </section>
  );
}
