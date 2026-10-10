/**
 * The chat controller: session lifecycle, streaming, and failure recovery.
 *
 * The hook owns the two things a component should not: **when a session is
 * opened** (lazily, on the first send, so opening the page costs nothing and
 * creates no rows) and **what a failure means** (mapped to plain language, with
 * the message that failed kept for Retry).
 *
 * Deliberate choices:
 *
 * - **No `EventSource`.** The stream is a POST with a bearer token.
 * - **One in-flight send.** A second send while streaming is ignored rather than
 *   queued: the backend does not serialise concurrent turns in a session, so two
 *   interleaved replies would be indistinguishable in history.
 * - **Retry resends the exact text** that failed. Nothing is reworded.
 * - **Offline is checked, not guessed.** `navigator.onLine` plus the `offline`
 *   event decide between "you appear to be offline" and "the connection
 *   dropped" — different sentences because they are different situations.
 */

import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";

import { api as defaultClient, type ApiClient } from "@/lib/api";

import { chatReducer, failureFor, failureFromStreamFailure, initialChatState } from "./chatState";
import { createChatSession } from "./endpoints";
import { streamChatMessage, toStreamFailure } from "./stream";
import { CHAT_LOCALES, type ChatLocale } from "./types";

let sequence = 0;
/** A local id for a turn. Never sent anywhere; the server assigns real ids. */
export function nextTurnId(prefix: string): string {
  sequence += 1;
  return `${prefix}-${sequence}`;
}

export interface UseChatOptions {
  client?: ApiClient;
  locale?: ChatLocale;
}

export interface ChatController {
  state: ReturnType<typeof initialChatState>;
  /** True when the browser says there is no network at all. */
  online: boolean;
  send: (text: string) => Promise<void>;
  retry: () => Promise<void>;
  dismissFailure: () => void;
  setSaveHistory: (value: boolean) => void;
  setLocale: (value: ChatLocale) => void;
  clearConversation: () => void;
  startNewChat: () => void;
}

function initialLocale(preferred: ChatLocale | undefined): ChatLocale {
  return preferred !== undefined && CHAT_LOCALES.includes(preferred) ? preferred : "en";
}

export function useChat(options: UseChatOptions = {}): ChatController {
  const client = options.client ?? defaultClient;
  const [state, dispatch] = useReducer(
    chatReducer,
    initialChatState(initialLocale(options.locale)),
  );
  const [online, setOnline] = useState<boolean>(() =>
    typeof navigator === "undefined" ? true : navigator.onLine !== false,
  );

  const abortRef = useRef<AbortController | null>(null);
  const lastRetryRef = useRef<string | null>(null);

  // Connection state. `online`/`offline` are the only honest signals a browser
  // offers; they are wrong often enough that a send still has to handle a
  // failure, which it does.
  useEffect(() => {
    const handleOnline = (): void => {
      setOnline(true);
    };
    const handleOffline = (): void => {
      setOnline(false);
    };
    window.addEventListener("online", handleOnline);
    window.addEventListener("offline", handleOffline);
    return () => {
      window.removeEventListener("online", handleOnline);
      window.removeEventListener("offline", handleOffline);
    };
  }, []);

  // Never leave a stream open behind an unmounted page.
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  const openSession = useCallback(async (): Promise<string | null> => {
    if (state.sessionId !== null) {
      return state.sessionId;
    }
    dispatch({ type: "session-creating" });
    try {
      const session = await createChatSession(state.saveHistory, client);
      dispatch({
        type: "session-created",
        sessionId: session.id,
        persistent: session.persistent,
      });
      return session.id;
    } catch (error) {
      dispatch({
        type: "session-failed",
        failure: failureFromStreamFailure(toStreamFailure(error), null, !online),
      });
      return null;
    }
  }, [client, online, state.saveHistory, state.sessionId]);

  const send = useCallback(
    async (rawText: string): Promise<void> => {
      const text = rawText.trim();
      if (text === "" || state.sending) {
        return;
      }

      if (!online) {
        lastRetryRef.current = text;
        dispatch({ type: "send-failed", failure: failureFor("offline", null, text) });
        return;
      }

      const sessionId = await openSession();
      if (sessionId === null) {
        lastRetryRef.current = text;
        return;
      }

      const userTurnId = nextTurnId("user");
      const assistantTurnId = nextTurnId("assistant");
      dispatch({ type: "send-start", id: userTurnId, text, assistantId: assistantTurnId });

      const controller = new AbortController();
      abortRef.current = controller;
      lastRetryRef.current = text;

      try {
        const outcome = await streamChatMessage({
          sessionId,
          message: text,
          locale: state.locale,
          client,
          signal: controller.signal,
          onEvent: (event) => {
            if (event.type === "token") {
              dispatch({ type: "token", id: assistantTurnId, text: event.text });
            }
          },
        });

        switch (outcome.kind) {
          case "final":
            dispatch({
              type: "final",
              id: assistantTurnId,
              reply: outcome.reply,
              replaced: outcome.replaced,
              metadata: outcome.metadata,
            });
            return;
          case "stream-error":
            dispatch({
              type: "send-failed",
              failure: failureFor("stream-error", null, text, outcome.message),
            });
            dispatch({ type: "interrupted", id: assistantTurnId, reason: "ended" });
            return;
          case "interrupted":
            dispatch({ type: "interrupted", id: assistantTurnId, reason: outcome.reason });
            return;
        }
      } catch (error) {
        // Only pre-stream failures land here (401/403/404/422/429/503, or the
        // fetch itself failing). The placeholder reply is dropped: no reply is
        // better than an empty bubble next to an error.
        dispatch({
          type: "send-failed",
          failure: failureFromStreamFailure(toStreamFailure(error), text, !online),
        });
        dispatch({ type: "interrupted", id: assistantTurnId, reason: "aborted" });
      } finally {
        abortRef.current = null;
      }
    },
    [client, online, openSession, state.locale, state.sending],
  );

  const retry = useCallback(async (): Promise<void> => {
    const text = lastRetryRef.current;
    if (text === null) {
      return;
    }
    await send(text);
  }, [send]);

  const dismissFailure = useCallback((): void => {
    dispatch({ type: "dismiss-failure" });
  }, []);

  const setSaveHistory = useCallback((value: boolean): void => {
    dispatch({ type: "set-save-history", value });
  }, []);

  const setLocale = useCallback((value: ChatLocale): void => {
    dispatch({ type: "set-locale", value });
  }, []);

  const clearConversation = useCallback((): void => {
    abortRef.current?.abort();
    dispatch({ type: "clear-conversation" });
  }, []);

  const startNewChat = useCallback((): void => {
    abortRef.current?.abort();
    abortRef.current = null;
    lastRetryRef.current = null;
    dispatch({ type: "new-chat" });
  }, []);

  return useMemo(
    () => ({
      state,
      online,
      send,
      retry,
      dismissFailure,
      setSaveHistory,
      setLocale,
      clearConversation,
      startNewChat,
    }),
    [
      state,
      online,
      send,
      retry,
      dismissFailure,
      setSaveHistory,
      setLocale,
      clearConversation,
      startNewChat,
    ],
  );
}
