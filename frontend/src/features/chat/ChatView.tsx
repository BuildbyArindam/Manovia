/**
 * The chat surface.
 *
 * Layout order is reading order: Private Mode (or the way into it), then the
 * controls, then anything that needs attention (offline, a failure), then the
 * conversation, then the composer, then the permanent "not a therapist" line.
 * Nothing here is decorative enough to be worth hiding on a small screen — at
 * 360px everything stacks and stays reachable.
 *
 * **Softened mode.** When the latest reply is a crisis reply the page drops its
 * decorative surface: the starter chips go away (suggesting "I had a bad day"
 * next to a helpline card is tone-deaf), the typing dots stop animating, and the
 * only thing emphasised is the CrisisCard. There is no emoji and no confetti
 * anywhere in this product, and this is the flag that keeps a future
 * celebration from landing on a safety reply.
 *
 * **Private Mode is a dynamic import.** The on-device stack — rules engine,
 * compiled-bundle loader, capability probe, download manager, crypto and panel —
 * is bigger than the whole /chat route budget from Day 25, so it lives in
 * `features/private/ui/PrivateModeProvider` and is loaded with `import()` the
 * first time the reader asks for Private Mode. What stays on the critical path
 * is one button (`PrivateModeEntry`), the reply badge and two localStorage
 * pointers. The standard-mode controller keeps running either way, so switching
 * Private Mode off returns to the conversation that was already on screen.
 */

import { Suspense, lazy, useCallback, useState, type ReactElement, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";

import { PageHeader } from "@/components/PageHeader";
import { api as defaultClient, type ApiClient } from "@/lib/api";
import type { PrivateChatController } from "@/features/private/session/usePrivateChat";
import type { PrivateModeController } from "@/features/private/session/usePrivateMode";
import { PrivateModeEntry } from "@/features/private/ui/PrivateModeEntry";
import { PrivateReplyBadge } from "@/features/private/ui/PrivateReplyBadge";
import { PRIVATE_COPY } from "@/features/private/ui/copy";
import {
  readPrivateEnabled,
  readPrivatePanelOpen,
  writePrivatePanelOpen,
} from "@/features/private/ui/preference";

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
import { useChat, type ChatController } from "./useChat";
import { useMoodHintPreference } from "./useMoodHint";

const PrivateModeProvider = lazy(() => import("@/features/private/ui/PrivateModeProvider"));

export interface ChatViewProps {
  /** Injectable so tests drive a real controller against a fake transport. */
  client?: ApiClient;
}

interface ChatBodyProps {
  client: ApiClient;
  /** Whichever controller should answer right now. */
  chat: ChatController;
  /** Null in standard mode: no badge, no voice notice, no panel. */
  privateMode: PrivateModeController | null;
  privateChat: PrivateChatController | null;
  /** The panel, or the way into it. Placed here so reading order is right. */
  panel: ReactNode;
  showMoodHint: boolean;
}

function ChatBody({
  client,
  chat,
  privateMode,
  privateChat,
  panel,
  showMoodHint,
}: ChatBodyProps): ReactElement {
  const navigate = useNavigate();
  const { state, online } = chat;
  const softened = isSoftened(state.turns);
  const privateEnabled = privateMode?.enabled === true;

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
      if (!privateEnabled || privateChat === null || turn.role !== "assistant") {
        return null;
      }
      if (turn.metadata === null) {
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
    [privateEnabled, privateChat],
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

      {panel}

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
        client={client}
        locale={state.locale}
        disabled={state.sending || !online || privateEnabled}
        onOpenSession={chat.openVoiceSession}
      />

      {privateEnabled ? (
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

export function ChatView({ client }: ChatViewProps): ReactElement {
  const resolvedClient = client ?? defaultClient;
  const [showMoodHint] = useMoodHintPreference();

  // The server controller always runs: React's rules forbid calling it
  // conditionally, and it is also the honest arrangement — the standard-mode
  // conversation survives a trip into Private Mode and back.
  const serverChat = useChat({ client: resolvedClient });

  // Whether the Private Mode chunk is mounted at all. Persisted, so somebody who
  // uses Private Mode does not have to find the button again after a reload.
  const [privateOpen, setPrivateOpen] = useState<boolean>(readPrivatePanelOpen);

  const openPrivatePanel = useCallback((): void => {
    writePrivatePanelOpen(true);
    setPrivateOpen(true);
  }, []);

  if (!privateOpen) {
    return (
      <ChatBody
        client={resolvedClient}
        chat={serverChat}
        privateMode={null}
        privateChat={null}
        panel={<PrivateModeEntry onOpen={openPrivatePanel} />}
        showMoodHint={showMoodHint}
      />
    );
  }

  // While the chunk loads the page stays usable in standard mode, with one line
  // saying what is happening: a blank chat page is worse than a late panel.
  const fallback = (
    <ChatBody
      client={resolvedClient}
      chat={serverChat}
      privateMode={null}
      privateChat={null}
      panel={
        <p
          data-testid="private-loading"
          className="rounded-2xl border border-border bg-surface p-4 text-sm text-ink-muted shadow-soft"
        >
          {PRIVATE_COPY.booting}
        </p>
      }
      showMoodHint={showMoodHint}
    />
  );

  return (
    <Suspense fallback={fallback}>
      <PrivateModeProvider
        serverChat={serverChat}
        locale={serverChat.state.locale}
        autoBoot={readPrivateEnabled()}
      >
        {(surface) => (
          <ChatBody
            client={resolvedClient}
            chat={surface.chat}
            privateMode={surface.mode}
            privateChat={surface.privateChat}
            panel={surface.panel}
            showMoodHint={showMoodHint}
          />
        )}
      </PrivateModeProvider>
    </Suspense>
  );
}
