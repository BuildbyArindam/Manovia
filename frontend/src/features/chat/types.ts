/**
 * The chat wire contract, mirrored from the backend.
 *
 * Every field here has a source in `backend/app/services/chat/types.py` or
 * `backend/app/api/v1/chat.py`; nothing is invented client-side. When the
 * backend renames something this file is where the compile error lands, which
 * is the point of keeping the shapes in one place.
 */

import type { CrisisMessage, CrisisResource, RiskLevel } from "@/lib/endpoints";

/** The three languages the UI and the pre-written crisis copy both support. */
export const CHAT_LOCALES = ["en", "hi", "bn"] as const;

export type ChatLocale = (typeof CHAT_LOCALES)[number];

export const LOCALE_LABELS: Readonly<Record<ChatLocale, string>> = {
  en: "English",
  hi: "हिन्दी (Hindi)",
  bn: "বাংলা (Bengali)",
};

/** Mirrors `ResponseType` — what kind of answer the person got. */
export type ResponseType = "normal" | "check_in" | "crisis" | "fallback";

/**
 * Mirrors `ChatMetadata`: everything the UI needs besides the words.
 *
 * The CrisisCard is rendered from `crisis` + `resources` + `emergency`; the
 * "softened UI" decision is `response_type === "crisis"`.
 */
export interface ChatMetadata {
  risk_level: RiskLevel;
  /** `null` on a crisis turn: the pipeline stops before emotion analysis. */
  emotion: string | null;
  response_type: ResponseType;
  resources: CrisisResource[];
  emergency: CrisisResource | null;
  /** Pre-written crisis copy, verbatim. Never reworded by the client. */
  crisis: CrisisMessage | null;
  check_in: CrisisMessage | null;
  /** True when the reply stands in for a model answer that could not be given. */
  degraded: boolean;
  /** True when this turn was written to the database. */
  persisted: boolean;
  about_someone_else: boolean;
  locale: string;
  region: string;
}

/** `POST /chat/sessions` response. */
export interface ChatSession {
  id: string;
  user_id: string;
  created_at: string;
  ended_at: string | null;
  persistent: boolean;
  expires_at: string | null;
}

/** One stored message, as returned by `GET …/messages`. */
export interface StoredMessage {
  id: string;
  role: "user" | "assistant" | "system";
  text: string;
  risk_level: number;
  emotion: string | null;
  created_at: string;
}

export interface StoredMessages {
  session_id: string;
  persistent: boolean;
  messages: StoredMessage[];
}

/**
 * The events a chat stream can carry.
 *
 * `token` events append; `final` is authoritative (when `replaced` is true the
 * rendered text must be swapped for `reply`); `error` only happens *after*
 * streaming started, because anything known earlier is an ordinary HTTP status.
 */
export type ChatStreamEvent =
  | { type: "token"; text: string }
  | {
      type: "final";
      reply: string;
      replaced: boolean;
      sessionId: string;
      messageId: string | null;
      metadata: ChatMetadata;
    }
  | { type: "error"; code: string; message: string }
  /** An event this build does not know. Ignored, never a crash. */
  | { type: "unknown"; event: string };

/** `message` in the request body; `region`/`locale` override the profile. */
export interface SendChatMessageInput {
  message: string;
  locale?: ChatLocale;
  region?: string | null;
}
