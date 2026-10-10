/**
 * The composer.
 *
 * Keyboard behaviour is the contract: **Enter sends, Shift+Enter makes a new
 * line**, and a textarea (not an input) so a long thought is still editable.
 * Two details that are easy to get wrong and matter here:
 *
 * - **IME composition.** Hindi and Bengali input methods emit `Enter` to commit
 *   a candidate. Sending on that keystroke would cut a word in half, so a
 *   composition in progress is never treated as "send".
 * - **The counter is a limit, not a threat.** The textarea carries `maxLength`,
 *   the counter counts down the last stretch in a stronger colour, and nothing
 *   is ever silently truncated — the person sees the number before the browser
 *   stops accepting keys.
 */

import { useId, useRef, useState, type KeyboardEvent, type ReactElement } from "react";

import { CHAT_COPY, MAX_MESSAGE_CHARS } from "./copy";

export interface ComposerProps {
  onSend: (text: string) => void;
  sending: boolean;
  disabled?: boolean;
  maxLength?: number;
}

/** The last stretch of the budget, where the counter gets louder. */
const NEAR_LIMIT_CHARS = 200;

export function Composer({
  onSend,
  sending,
  disabled = false,
  maxLength = MAX_MESSAGE_CHARS,
}: ComposerProps): ReactElement {
  const [text, setText] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const labelId = useId();
  const hintId = useId();
  const counterId = useId();

  const trimmed = text.trim();
  const remaining = maxLength - text.length;
  const tooLong = remaining < 0;
  const canSend = trimmed !== "" && !sending && !disabled && !tooLong;

  function submit(): void {
    if (!canSend) {
      return;
    }
    onSend(trimmed);
    setText("");
    // Keep the focus where it was: a keyboard user should be able to type the
    // next sentence without hunting for the field again.
    textareaRef.current?.focus();
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>): void {
    if (event.key !== "Enter" || event.shiftKey || event.altKey || event.metaKey || event.ctrlKey) {
      return;
    }
    // An IME is composing (Devanagari/Bengali input): Enter commits the
    // candidate, it does not send the message.
    if (event.nativeEvent.isComposing === true) {
      return;
    }
    event.preventDefault();
    submit();
  }

  return (
    <div className="rounded-2xl border border-border bg-surface p-4 shadow-soft">
      <label id={labelId} htmlFor="chat-input" className="block font-semibold">
        {CHAT_COPY.composerLabel}
      </label>
      <textarea
        id="chat-input"
        ref={textareaRef}
        rows={3}
        value={text}
        maxLength={maxLength}
        disabled={disabled}
        placeholder={CHAT_COPY.composerPlaceholder}
        aria-labelledby={labelId}
        aria-describedby={`${hintId} ${counterId}`}
        onKeyDown={handleKeyDown}
        onChange={(event) => {
          setText(event.target.value);
        }}
        className="mt-2 w-full resize-y rounded-xl border border-border-strong bg-surface-muted p-3 text-ink disabled:opacity-60"
      />
      <div className="mt-2 flex items-end justify-between gap-3">
        <p id={hintId} className="text-sm text-ink-muted">
          Enter sends · Shift+Enter adds a new line
        </p>
        <p
          id={counterId}
          data-testid="char-counter"
          className={
            remaining <= NEAR_LIMIT_CHARS
              ? "shrink-0 text-sm font-semibold text-danger"
              : "shrink-0 text-sm text-ink-muted"
          }
        >
          {Math.max(text.length, 0)} / {maxLength}
        </p>
      </div>
      <button
        type="button"
        data-testid="send-button"
        disabled={!canSend}
        onClick={submit}
        className="mt-3 inline-flex min-h-12 w-full items-center justify-center rounded-xl bg-accent-bg px-5 py-3 font-semibold text-accent-fg disabled:opacity-50 sm:w-auto"
      >
        {sending ? CHAT_COPY.sending : CHAT_COPY.send}
      </button>
    </div>
  );
}
