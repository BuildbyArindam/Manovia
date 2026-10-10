/**
 * The conversation as a pure reducer.
 *
 * Everything the UI shows is derived from this state, and every change to it is
 * one action, so the streaming path (tokens arriving out of the network's
 * control), the crisis path and the failure paths can all be tested by feeding
 * actions in — no timers, no fetch, no React.
 *
 * Two invariants the tests hold this to:
 *
 * - a **crisis** turn renders from `metadata.crisis`/`resources`, never from
 *   model text, and sets `softened` for the rest of the page;
 * - a **failed or interrupted** turn keeps the words that did arrive and says
 *   the reply is incomplete, rather than silently presenting half a sentence as
 *   an answer.
 */

import { CHAT_ERRORS } from "./copy";
import type { StreamFailure } from "./stream";
import type { ChatLocale, ChatMetadata } from "./types";

export type FailureKind =
  | "offline"
  | "network"
  | "stream-interrupted"
  | "stream-error"
  | "consent"
  | "rate-limited"
  | "session"
  | "unknown";

export interface ChatFailure {
  kind: FailureKind;
  title: string;
  body: string;
  action: string;
  /** The backend's `X-Request-ID`, quoted so a problem can be traced. */
  requestId: string | null;
  /** The text that failed, so "Try again" resends exactly that. */
  retryMessage: string | null;
}

export interface ChatTurn {
  id: string;
  role: "user" | "assistant";
  text: string;
  /** True while tokens are still arriving for this assistant turn. */
  streaming: boolean;
  /** True when `final.reply` replaced the streamed text. */
  replaced: boolean;
  /** True when the reply was cut off and is not the whole answer. */
  incomplete: boolean;
  metadata: ChatMetadata | null;
}

export interface ChatState {
  sessionId: string | null;
  /** What the *current* session really is, as the server reported it. */
  persistent: boolean;
  /** The "Save this conversation" toggle: applies to the *next* session. */
  saveHistory: boolean;
  locale: ChatLocale;
  turns: ChatTurn[];
  sending: boolean;
  failure: ChatFailure | null;
}

export type ChatAction =
  | { type: "session-creating" }
  | { type: "session-created"; sessionId: string; persistent: boolean }
  | { type: "session-failed"; failure: ChatFailure }
  | { type: "send-start"; id: string; text: string; assistantId: string }
  | { type: "token"; id: string; text: string }
  | { type: "final"; id: string; reply: string; replaced: boolean; metadata: ChatMetadata }
  | { type: "interrupted"; id: string; reason: "aborted" | "network" | "ended" }
  | { type: "send-failed"; failure: ChatFailure }
  | { type: "dismiss-failure" }
  | { type: "set-save-history"; value: boolean }
  | { type: "set-locale"; value: ChatLocale }
  | { type: "clear-conversation" }
  | { type: "new-chat" };

export function initialChatState(locale: ChatLocale = "en"): ChatState {
  return {
    sessionId: null,
    persistent: false,
    saveHistory: false,
    locale,
    turns: [],
    sending: false,
    failure: null,
  };
}

function mapTurn(
  turns: readonly ChatTurn[],
  id: string,
  update: (turn: ChatTurn) => ChatTurn,
): ChatTurn[] {
  return turns.map((turn) => (turn.id === id ? update(turn) : turn));
}

export function chatReducer(state: ChatState, action: ChatAction): ChatState {
  switch (action.type) {
    case "session-creating":
      return { ...state, failure: null };

    case "session-created":
      return {
        ...state,
        sessionId: action.sessionId,
        persistent: action.persistent,
        failure: null,
      };

    case "session-failed":
      return { ...state, sending: false, failure: action.failure };

    case "send-start": {
      const user: ChatTurn = {
        id: action.id,
        role: "user",
        text: action.text,
        streaming: false,
        replaced: false,
        incomplete: false,
        metadata: null,
      };
      const assistant: ChatTurn = {
        id: action.assistantId,
        role: "assistant",
        text: "",
        streaming: true,
        replaced: false,
        incomplete: false,
        metadata: null,
      };
      return { ...state, sending: true, failure: null, turns: [...state.turns, user, assistant] };
    }

    case "token":
      return {
        ...state,
        turns: mapTurn(state.turns, action.id, (turn) => ({
          ...turn,
          text: turn.text + action.text,
        })),
      };

    case "final":
      return {
        ...state,
        sending: false,
        turns: mapTurn(state.turns, action.id, (turn) => ({
          ...turn,
          // `final.reply` wins. When `replaced` is true the streamed tokens
          // were not the answer and must not survive.
          text: action.reply,
          replaced: action.replaced,
          streaming: false,
          incomplete: false,
          metadata: action.metadata,
        })),
      };

    case "interrupted":
      // An abort the caller asked for (a new chat mid-stream) is not an error:
      // drop the placeholder rather than label a deliberate stop as a failure.
      if (action.reason === "aborted") {
        return {
          ...state,
          sending: false,
          turns: state.turns.filter((turn) => turn.id !== action.id),
        };
      }
      return {
        ...state,
        sending: false,
        failure: failureFor("stream-interrupted", null, null),
        turns: mapTurn(state.turns, action.id, (turn) => ({
          ...turn,
          streaming: false,
          incomplete: true,
        })),
      };

    case "send-failed":
      return { ...state, sending: false, failure: action.failure };

    case "dismiss-failure":
      return { ...state, failure: null };

    case "set-save-history":
      // The toggle cannot retroactively save (or unsave) a session that exists.
      return state.sessionId === null
        ? { ...state, saveHistory: action.value, failure: null }
        : { ...state, saveHistory: action.value };

    case "set-locale":
      return { ...state, locale: action.value };

    case "clear-conversation":
      return { ...state, turns: [], failure: null };

    case "new-chat":
      return {
        ...state,
        sessionId: null,
        persistent: false,
        turns: [],
        sending: false,
        failure: null,
      };
  }
}

/** Build a failure from the client-side copy for one kind. */
export function failureFor(
  kind: FailureKind,
  requestId: string | null,
  retryMessage: string | null,
  bodyOverride?: string,
): ChatFailure {
  const copy = {
    offline: CHAT_ERRORS.offline,
    network: CHAT_ERRORS.network,
    "stream-interrupted": CHAT_ERRORS.streamInterrupted,
    "stream-error": CHAT_ERRORS.streamError,
    consent: CHAT_ERRORS.consent,
    "rate-limited": CHAT_ERRORS.rateLimited,
    session: CHAT_ERRORS.session,
    unknown: CHAT_ERRORS.unknown,
  }[kind];
  return {
    kind,
    title: copy.title,
    // The backend's curated message is already plain language; when it has one,
    // prefer it over a second copy that could drift out of step.
    body: bodyOverride ?? copy.body,
    action: copy.action,
    requestId,
    retryMessage,
  };
}

/** Map a pre-stream failure (HTTP status + code) onto a plain-language one. */
export function failureFromStreamFailure(
  failure: StreamFailure,
  retryMessage: string | null,
  offline: boolean,
): ChatFailure {
  if (failure.kind === "network") {
    if (failure.code === "aborted") {
      return failureFor("unknown", null, null);
    }
    return failureFor(offline ? "offline" : "network", null, retryMessage);
  }
  const requestId = failure.requestId;
  const message = failure.message === "" ? undefined : failure.message;
  switch (failure.code) {
    case "consent_required":
      return failureFor("consent", requestId, retryMessage);
    case "rate_limited":
      return failureFor("rate-limited", requestId, retryMessage, message);
    case "session_not_found":
    case "session_ended":
      return failureFor("session", requestId, null);
    case "message_empty":
    case "message_too_long":
    case "message_encoding":
      return failureFor("unknown", requestId, null, message);
    default:
      return failureFor("unknown", requestId, retryMessage, message);
  }
}

/** The latest assistant turn, or `null` before the first reply. */
export function latestAssistantTurn(turns: readonly ChatTurn[]): ChatTurn | null {
  for (let index = turns.length - 1; index >= 0; index -= 1) {
    const turn = turns[index];
    if (turn !== undefined && turn.role === "assistant") {
      return turn;
    }
  }
  return null;
}

/**
 * True when the page should soften: no emoji, no confetti, no cheerful colour.
 *
 * A crisis reply is not the only trigger — an imminent-risk turn answered with
 * the pre-written template must soften the page too, even if a future backend
 * labels it differently.
 */
export function isSoftened(turns: readonly ChatTurn[]): boolean {
  const latest = latestAssistantTurn(turns);
  if (latest === null || latest.metadata === null) {
    return false;
  }
  return (
    latest.metadata.response_type === "crisis" ||
    latest.metadata.risk_level === "high" ||
    latest.metadata.risk_level === "imminent"
  );
}
