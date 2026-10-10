/**
 * The chat endpoints: open a session, read its history.
 *
 * Sending a message is deliberately **not** here — it streams, and lives in
 * `stream.ts` with the SSE reader it needs.
 */

import { api as defaultClient, type ApiClient } from "@/lib/api";

import type { ChatLocale, ChatSession, StoredMessages } from "./types";

export const CHAT_ENDPOINTS = {
  sessions: "/chat/sessions",
  messages: (sessionId: string): string => `/chat/sessions/${sessionId}/messages`,
  stream: (sessionId: string): string => `/chat/sessions/${sessionId}/stream`,
} as const;

/**
 * Open a chat session.
 *
 * `saveHistory` is the "Save this conversation" toggle: `false` (the default)
 * gives an ephemeral session that leaves no rows behind; `true` needs the
 * `store_chat` consent on the account and the backend answers
 * `403 consent_required` without it.
 */
export async function createChatSession(
  saveHistory: boolean,
  client: ApiClient = defaultClient,
): Promise<ChatSession> {
  return client.post<ChatSession>(CHAT_ENDPOINTS.sessions, { save_history: saveHistory });
}

/** The newest `limit` messages of a session, oldest first. Owner only. */
export async function fetchChatMessages(
  sessionId: string,
  client: ApiClient = defaultClient,
  limit = 100,
): Promise<StoredMessages> {
  return client.get<StoredMessages>(`${CHAT_ENDPOINTS.messages(sessionId)}?limit=${limit}`);
}

/** The body of `POST /chat/sessions/{id}/stream`. */
export interface StreamRequestBody {
  message: string;
  locale?: ChatLocale;
}
