/**
 * The chat surface.
 *
 * Layout order is reading order: controls, then anything that needs attention
 * (offline, a failure), then the conversation, then the composer, then the
 * permanent "not a therapist" line. Nothing here is decorative enough to be
 * worth hiding on a small screen — at 360px everything stacks and stays
 * reachable.
 *
 * **Softened mode.** When the latest reply is a crisis reply the page drops its
 * decorative surface: the starter chips go away (suggesting "I had a bad day"
 * next to a helpline card is tone-deaf), the typing dots stop animating, and the
 * only thing emphasised is the CrisisCard. There is no emoji and no confetti
 * anywhere in this product, and this is the flag that keeps a future
 * celebration from landing on a safety reply.
 */

import { useCallback, type ReactElement } from "react";
import { useNavigate } from "react-router-dom";

import { PageHeader } from "@/components/PageHeader";
import type { ApiClient } from "@/lib/api";

import { ChatControls } from "./ChatControls";
import { ChatFailureBanner, OfflineNotice } from "./ChatFailureBanner";
import { CompanionNotice } from "./CompanionNotice";
import { Composer } from "./Composer";
import { MessageList } from "./MessageList";
import { StarterChips } from "./StarterChips";
import { CHAT_COPY } from "./copy";
import { isSoftened } from "./chatState";
import { useAutoScroll } from "./useAutoScroll";
import { useChat } from "./useChat";
import { useMoodHintPreference } from "./useMoodHint";

export interface ChatViewProps {
  /** Injectable so tests drive a real controller against a fake transport. */
  client?: ApiClient;
}

export function ChatView({ client }: ChatViewProps): ReactElement {
  const chat = useChat(client === undefined ? {} : { client });
  const navigate = useNavigate();
  const [showMoodHint] = useMoodHintPreference();

  const { state, online } = chat;
  const softened = isSoftened(state.turns);

  // Follow the reply only while the reader is at the bottom.
  const lastTurn = state.turns[state.turns.length - 1];
  const scroll = useAutoScroll(lastTurn === undefined ? "" : `${lastTurn.id}:${lastTurn.text}`);

  const handleFailureAction = useCallback((): void => {
    const failure = state.failure;
    if (failure === null) {
      return;
    }
    switch (failure.kind) {
      case "consent":
        navigate("/settings");
        return;
      case "session":
        chat.startNewChat();
        return;
      default:
        void chat.retry();
    }
  }, [chat, navigate, state.failure]);

  return (
    <div className="space-y-4" data-testid="chat-view" data-softened={softened ? "true" : "false"}>
      <PageHeader title={CHAT_COPY.heading} lede={CHAT_COPY.lede} />

      <ChatControls
        saveHistory={state.saveHistory}
        sessionIsPersistent={state.persistent}
        hasSession={state.sessionId !== null}
        locale={state.locale}
        disabled={state.sending}
        onSaveHistoryChange={chat.setSaveHistory}
        onClear={chat.clearConversation}
        onNewChat={chat.startNewChat}
        onLocaleChange={chat.setLocale}
      />

      {online ? null : <OfflineNotice />}

      {state.failure === null ? null : (
        <ChatFailureBanner
          failure={state.failure}
          onAction={handleFailureAction}
          onDismiss={chat.dismissFailure}
        />
      )}

      <MessageList
        turns={state.turns}
        showMoodHint={showMoodHint}
        containerRef={scroll.containerRef}
        pinned={scroll.pinned}
        onJumpToLatest={scroll.scrollToLatest}
      />

      {state.turns.length === 0 && !softened ? (
        <StarterChips onSelect={(text) => void chat.send(text)} disabled={state.sending} />
      ) : null}

      <Composer onSend={(text) => void chat.send(text)} sending={state.sending} />

      <CompanionNotice />
    </div>
  );
}
