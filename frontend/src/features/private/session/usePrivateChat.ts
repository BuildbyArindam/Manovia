/**
 * A `ChatController` that never touches the network.
 *
 * `ChatView` is written against `ChatController`, so Private Mode supplies the same
 * shape from a `PrivateSession` instead of from the API client: same reducer, same
 * turn lifecycle (`send-start` → `token`* → `final`), same failure handling, same
 * CrisisCard. Nothing in the chat UI has to know which of the two is answering,
 * except for the parts that must be honest about it — the private badge and the
 * "not stored" state, which read the turn's metadata.
 *
 * Two deliberate differences from the server controller:
 *
 *   - `openVoiceSession()` returns null. Voice runs through the server's realtime
 *     endpoint; there is no on-device speech pipeline, so Private Mode says it has no
 *     voice rather than quietly sending audio somewhere.
 *   - `setSaveHistory` maps to *local encrypted* storage, and the caller is told, so
 *     the toggle's label can say where the conversation would be kept.
 */

import { useCallback, useReducer, useRef, useState } from "react";

import type { ChatLocale } from "@/features/chat/types";
import { chatReducer, initialChatState } from "@/features/chat/chatState";
import type { ChatController } from "@/features/chat/useChat";

import type { PrivateDiagnostics, PrivateSession } from "./privateSession";
import { PrivateSessionError } from "./privateSession";

/**
 * What the chat surface gets from Private Mode: a normal `ChatController`, plus
 * the two things that make the on-device path honest — per-turn diagnostics and
 * the label of whatever actually answered.
 */
export interface PrivateChatController extends ChatController {
  /** Keyed by assistant turn id. Read during the render the `final` action causes. */
  diagnostics: ReadonlyMap<string, PrivateDiagnostics>;
  /** e.g. `"template writer (en, check-in)"` or `"small on-device model (6 tok/s)"`. */
  runtimeLabel: string | null;
}

export interface UsePrivateChatOptions {
  /** Null while Private Mode is booting or switched off. */
  session: PrivateSession | null;
  locale?: ChatLocale;
  /** Called when the user toggles "keep this conversation on this device". */
  onStoreTranscriptChange?: (value: boolean) => void;
}

let counter = 0;
function nextId(prefix: string): string {
  counter += 1;
  return `private-${prefix}-${counter}`;
}

export function usePrivateChat(options: UsePrivateChatOptions): PrivateChatController {
  const { session } = options;
  const [state, dispatch] = useReducer(chatReducer, initialChatState(options.locale ?? "en"));
  const [runtimeLabel, setRuntimeLabel] = useState<string | null>(null);
  const lastMessageRef = useRef<string | null>(null);
  const diagnosticsRef = useRef(new Map<string, PrivateDiagnostics>());

  const run = useCallback(
    async (text: string): Promise<void> => {
      if (session === null) {
        dispatch({
          type: "send-failed",
          failure: {
            kind: "unknown",
            title: "Private Mode is still starting",
            body: "The on-device safety rules are loading. Try again in a moment.",
            action: "Try again",
            requestId: null,
            retryMessage: text,
          },
        });
        return;
      }
      const userId = nextId("user");
      const assistantId = nextId("assistant");
      lastMessageRef.current = text;
      dispatch({ type: "send-start", id: userId, text, assistantId });
      try {
        const turn = await session.send(text, {
          onToken: (piece) => dispatch({ type: "token", id: assistantId, text: piece }),
        });
        // Recorded before the dispatch, so the render that shows the reply can
        // already see how it was produced.
        diagnosticsRef.current.set(assistantId, turn.diagnostics);
        setRuntimeLabel(turn.diagnostics.runtimeLabel);
        dispatch({
          type: "final",
          id: assistantId,
          reply: turn.reply,
          // True when the guard replaced what the model wrote, exactly as the
          // server's `final` event means it.
          replaced: turn.diagnostics.guard !== null && !turn.diagnostics.guard.passed,
          metadata: turn.metadata,
        });
      } catch (error) {
        const empty = error instanceof PrivateSessionError && error.code === "message_empty";
        dispatch({
          type: "send-failed",
          failure: {
            kind: "unknown",
            title: empty ? "There is nothing to answer yet" : "Private Mode could not answer that",
            body:
              error instanceof Error
                ? error.message
                : "An on-device runtime failed and no fallback was available.",
            action: empty ? "Close" : "Try again",
            requestId: null,
            retryMessage: empty ? null : text,
          },
        });
      }
    },
    [session],
  );

  const send = useCallback(
    async (text: string): Promise<void> => {
      await run(text);
    },
    [run],
  );

  const retry = useCallback(async (): Promise<void> => {
    const text = lastMessageRef.current;
    if (text === null) {
      return;
    }
    await run(text);
  }, [run]);

  const dismissFailure = useCallback((): void => {
    dispatch({ type: "dismiss-failure" });
  }, []);

  const setSaveHistory = useCallback(
    (value: boolean): void => {
      dispatch({ type: "set-save-history", value });
      options.onStoreTranscriptChange?.(value);
    },
    // The callback is a prop, not a dependency worth re-creating `send` for.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  const setLocale = useCallback((value: ChatLocale): void => {
    dispatch({ type: "set-locale", value });
  }, []);

  const clearConversation = useCallback((): void => {
    dispatch({ type: "clear-conversation" });
    diagnosticsRef.current.clear();
    session?.reset();
  }, [session]);

  const startNewChat = useCallback((): void => {
    dispatch({ type: "new-chat" });
    diagnosticsRef.current.clear();
    session?.reset();
    lastMessageRef.current = null;
  }, [session]);

  const openVoiceSession = useCallback(async (): Promise<string | null> => {
    // Voice has no on-device pipeline: returning a session id would mean sending
    // audio to the server, which Private Mode does not do.
    return null;
  }, []);

  return {
    state,
    // There is no network in this path, so "online" is always true: a private
    // conversation works in airplane mode, which is the point.
    online: true,
    send,
    openVoiceSession,
    retry,
    dismissFailure,
    setSaveHistory,
    setLocale,
    clearConversation,
    startNewChat,
    diagnostics: diagnosticsRef.current,
    runtimeLabel,
  };
}
