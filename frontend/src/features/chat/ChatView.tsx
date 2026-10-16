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

import { useCallback, useEffect, type ReactElement } from "react";
import { useNavigate } from "react-router-dom";

import { PageHeader } from "@/components/PageHeader";
import { api as defaultClient, type ApiClient } from "@/lib/api";
import { usePrivateChat } from "@/features/private/session/usePrivateChat";
import { usePrivateMode } from "@/features/private/session/usePrivateMode";
import { PrivateModePanel } from "@/features/private/ui/PrivateModePanel";
import { PrivateReplyBadge } from "@/features/private/ui/PrivateReplyBadge";
import { PRIVATE_COPY } from "@/features/private/ui/copy";

import { ChatControls } from "./ChatControls";
import { ChatFailureBanner, OfflineNotice } from "./ChatFailureBanner";
import { CompanionNotice } from "./CompanionNotice";
import { Composer } from "./Composer";
import { MessageList } from "./MessageList";
import { StarterChips } from "./StarterChips";
import { VoiceCompanion } from "./VoiceCompanion";
import { CHAT_COPY } from "./copy";
import { isSoftened, type ChatTurn } from "./chatState";
import { useAutoScroll } from "./useAutoScroll";
import { useChat } from "./useChat";
import { useMoodHintPreference } from "./useMoodHint";

export interface ChatViewProps {
  /** Injectable so tests drive a real controller against a fake transport. */
  client?: ApiClient;
}

export function ChatView({ client }: ChatViewProps): ReactElement {
  const resolvedClient = client ?? defaultClient;
  const navigate = useNavigate();
  const [showMoodHint] = useMoodHintPreference();

  // Both controllers always run: React's rules forbid calling one conditionally,
  // and it is also the honest arrangement — switching Private Mode on or off must
  // never lose the conversation that is already on screen.
  const serverChat = useChat({ client: resolvedClient });
  const privateMode = usePrivateMode();
  const privateChat = usePrivateChat({
    session: privateMode.enabled ? privateMode.session : null,
    locale: serverChat.state.locale,
    onStoreTranscriptChange: (value) => {
      void privateMode.setStoreTranscript(value);
    },
  });
  const chat = privateMode.enabled ? privateChat : serverChat;

  // The app's language picker drives the private session too: helplines, crisis
  // copy and the template writer all read the locale from private settings.
  useEffect(() => {
    if (!privateMode.enabled) {
      return;
    }
    void privateMode.setLocale(serverChat.state.locale);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [privateMode.enabled, serverChat.state.locale]);

  const { state, online } = chat;
  const softened = isSoftened(state.turns);

  // Follow the reply only while the reader is at the bottom.
  const lastTurn = state.turns[state.turns.length - 1];
  const scroll = useAutoScroll(lastTurn === undefined ? "" : `${lastTurn.id}:${lastTurn.text}`);

  /**
   * Every private reply is labelled with what actually wrote it and whether it was
   * stored. Standard-mode turns get nothing: their bubble already says which
   * service answered.
   */
  const privateTurnFooter = useCallback(
    (turn: ChatTurn): ReactElement | null => {
      if (!privateMode.enabled || turn.role !== "assistant" || turn.metadata === null) {
        return null;
      }
      const diagnostics = privateChat.diagnostics.get(turn.id);
      const guard = diagnostics?.guard;
      return (
        <PrivateReplyBadge
          metadata={turn.metadata}
          runtimeLabel={diagnostics?.runtimeLabel ?? null}
          guardReplaced={guard !== undefined && guard !== null && !guard.passed}
          timings={
            diagnostics === undefined
              ? null
              : {
                  assessedInMs: diagnostics.assessedInMs,
                  repliedInMs: diagnostics.repliedInMs,
                  totalMs: diagnostics.totalMs,
                  tokensPerSecond: diagnostics.tokensPerSecond,
                  tokens: diagnostics.tokens,
                }
          }
        />
      );
    },
    [privateMode.enabled, privateChat.diagnostics],
  );

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

      <PrivateModePanel controller={privateMode} />

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

      <VoiceCompanion
        client={resolvedClient}
        locale={state.locale}
        disabled={state.sending || !online || privateMode.enabled}
        onOpenSession={chat.openVoiceSession}
      />

      {privateMode.enabled ? (
        <p data-testid="private-voice-notice" className="text-sm text-ink-muted">
          {PRIVATE_COPY.voiceUnavailable}
        </p>
      ) : null}

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
        renderTurnFooter={privateTurnFooter}
      />

      {state.turns.length === 0 && !softened ? (
        <StarterChips onSelect={(text) => void chat.send(text)} disabled={state.sending} />
      ) : null}

      <Composer onSend={(text) => void chat.send(text)} sending={state.sending} />

      <CompanionNotice />
    </div>
  );
}
