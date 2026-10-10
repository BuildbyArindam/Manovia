/**
 * One message bubble.
 *
 * Accessibility is the reason this component is more than a `<div>`:
 *
 * - **Every bubble names its speaker** for a screen reader ("You said", "Manovia
 *   said"). Colour and alignment cannot carry that: a blind user has no other
 *   way to tell who is talking in a list of paragraphs.
 * - **The latest assistant message is the live region** (`aria-live="polite"`,
 *   `aria-atomic="true"`). While tokens stream in, its accessible content is the
 *   single phrase "Manovia is replying…" and the visible text is `aria-hidden`;
 *   when the reply finishes, the accessible content becomes the reply. That is
 *   one announcement per turn instead of one per token, which is what WCAG 4.1.3
 *   actually asks for — a status message that interrupts constantly is worse
 *   than none.
 * - **A crisis turn carries its card**, and the card is inside the same list
 *   item, so the reading order is: the reply, then the helplines. Nothing about
 *   the pre-written copy is rewritten here (AGENTS.md safety rule 2).
 */

import type { ReactElement } from "react";

import { CrisisCard } from "@/components/CrisisCard";

import { CHAT_COPY } from "./copy";
import { TypingIndicator } from "./TypingIndicator";
import { moodHintFor } from "./preferences";
import type { ChatTurn } from "./chatState";

export interface MessageBubbleProps {
  turn: ChatTurn;
  /** The latest assistant turn owns the live region; the others are inert. */
  isLatestAssistant?: boolean;
  /** Show the emotion hint (Settings → "Show how I read your mood"). */
  showMoodHint?: boolean;
}

export function MessageBubble({
  turn,
  isLatestAssistant = false,
  showMoodHint = false,
}: MessageBubbleProps): ReactElement {
  const isUser = turn.role === "user";
  const label = isUser ? CHAT_COPY.youSaid : CHAT_COPY.manoviaSaid;
  const streaming = turn.streaming;
  const awaiting = streaming && turn.text === "";
  const metadata = turn.metadata;
  const isCrisis = metadata !== null && metadata.response_type === "crisis";

  const moodHint = showMoodHint && !isUser ? moodHintFor(metadata?.emotion ?? null) : null;

  return (
    <li
      data-testid="chat-message"
      data-role={turn.role}
      {...(isLatestAssistant && !isUser ? { "aria-live": "polite", "aria-atomic": "true" } : {})}
      className={isUser ? "flex justify-end" : "flex justify-start"}
    >
      <div
        data-testid={isUser ? "user-bubble" : "assistant-bubble"}
        className={
          isUser
            ? "max-w-[85%] rounded-2xl rounded-br-sm bg-accent-bg px-4 py-3 text-accent-fg sm:max-w-[70%]"
            : isCrisis
              ? "max-w-[92%] rounded-2xl rounded-bl-sm border border-border-strong bg-surface-muted px-4 py-3 sm:max-w-[85%]"
              : "max-w-[92%] rounded-2xl rounded-bl-sm bg-surface px-4 py-3 shadow-soft sm:max-w-[85%]"
        }
      >
        {/* The speaker, for assistive technology. */}
        <p className="sr-only">{label}:</p>

        {isUser ? (
          <p className="whitespace-pre-wrap break-words">{turn.text}</p>
        ) : awaiting ? (
          <>
            <p className="sr-only">{CHAT_COPY.typingAnnouncement}</p>
            <TypingIndicator />
          </>
        ) : streaming ? (
          <>
            <p className="sr-only">{CHAT_COPY.typingAnnouncement}</p>
            <p aria-hidden="true" className="whitespace-pre-wrap break-words">
              {turn.text}
            </p>
          </>
        ) : (
          <p data-testid="assistant-text" className="whitespace-pre-wrap break-words">
            {turn.text}
          </p>
        )}

        {turn.incomplete ? (
          <p className="mt-2 text-sm text-ink-muted">{CHAT_COPY.incompleteReply}</p>
        ) : null}

        {metadata?.degraded === true ? (
          <p className="mt-2 text-sm text-ink-muted">{CHAT_COPY.degradedNotice}</p>
        ) : null}

        {moodHint !== null ? (
          <p data-testid="mood-hint" className="mt-2 text-xs text-ink-muted">
            {moodHint}
          </p>
        ) : null}

        {isCrisis && metadata !== null ? (
          <div data-testid="crisis-turn" className="mt-4">
            <CrisisCard
              message={metadata.crisis}
              resources={metadata.resources}
              emergency={metadata.emergency}
              disclaimer={metadata.crisis?.disclaimer ?? null}
              testId="chat-crisis-card"
            />
          </div>
        ) : null}
      </div>
    </li>
  );
}
