/**
 * The message list.
 *
 * An ordered list of turns with the scroll container around it, so the
 * auto-scroll logic has one element to measure. The "Jump to latest" button
 * exists because auto-scroll stops the moment the reader scrolls up (see
 * `useAutoScroll`): they must be able to come back without scrolling by hand.
 */

import type { ReactElement, RefObject } from "react";

import { MessageBubble } from "./MessageBubble";
import { CHAT_COPY } from "./copy";
import { latestAssistantTurn, type ChatTurn } from "./chatState";

export interface MessageListProps {
  turns: readonly ChatTurn[];
  showMoodHint: boolean;
  containerRef: RefObject<HTMLElement>;
  pinned: boolean;
  onJumpToLatest: () => void;
}

export function MessageList({
  turns,
  showMoodHint,
  containerRef,
  pinned,
  onJumpToLatest,
}: MessageListProps): ReactElement {
  const latest = latestAssistantTurn(turns);

  return (
    <div className="relative">
      <div
        ref={containerRef as RefObject<HTMLDivElement>}
        data-testid="chat-scroll-region"
        tabIndex={0}
        role="group"
        aria-label="Conversation"
        className="max-h-[52vh] min-h-16 overflow-y-auto rounded-2xl px-1 py-2 focus-visible:outline-2"
      >
        {turns.length === 0 ? (
          <p data-testid="chat-empty" className="px-3 py-4 text-ink-muted">
            Nothing here yet. Say as much or as little as you like — there is no wrong way to start.
          </p>
        ) : (
          <ol className="space-y-3">
            {turns.map((turn) => (
              <MessageBubble
                key={turn.id}
                turn={turn}
                isLatestAssistant={latest !== null && turn.id === latest.id}
                showMoodHint={showMoodHint}
              />
            ))}
          </ol>
        )}
      </div>

      {pinned ? null : (
        <div className="pointer-events-none mt-2 flex justify-center">
          <button
            type="button"
            data-testid="jump-to-latest"
            onClick={onJumpToLatest}
            className="pointer-events-auto inline-flex min-h-12 items-center rounded-full border border-border-strong bg-surface px-4 py-2 text-sm font-semibold shadow-soft"
          >
            {CHAT_COPY.jumpToLatest}
          </button>
        </div>
      )}
    </div>
  );
}
