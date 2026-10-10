/**
 * Reading one streamed reply.
 *
 * `POST /chat/sessions/{id}/stream` answers `200 text/event-stream` and the body
 * is read here with a `ReadableStream` reader, because `EventSource` cannot do
 * a POST with an `Authorization` header. Three things this module is careful
 * about:
 *
 * 1. **Chunk boundaries are arbitrary.** Every byte goes through the SSE parser,
 *    which only dispatches complete frames.
 * 2. **A dropped connection is not an empty reply.** Whatever tokens arrived are
 *    kept and reported as `interrupted`, so the UI can say "this reply was cut
 *    off" instead of showing a bubble that silently stops mid-sentence.
 * 3. **`final.reply` is authoritative.** When `replaced` is true the caller must
 *    swap the rendered text, because the streamed tokens were not the answer.
 */

import { isApiError, type ApiClient } from "@/lib/api";

import { CHAT_ENDPOINTS } from "./endpoints";
import { createSseParser, type SseFrame } from "./sse";
import type { ChatLocale, ChatMetadata, ChatStreamEvent } from "./types";

export interface StreamChatInput {
  sessionId: string;
  message: string;
  locale: ChatLocale;
  client: ApiClient;
  signal?: AbortSignal;
  onEvent: (event: ChatStreamEvent) => void;
}

/** What a stream attempt ended with. Never throws for a mid-stream failure. */
export type StreamOutcome =
  | { kind: "final"; reply: string; replaced: boolean; metadata: ChatMetadata }
  /** An `event: error` frame — a failure the server reported after it began. */
  | { kind: "stream-error"; code: string; message: string }
  /** The socket died, the tab aborted, or the body ended with no `final`. */
  | { kind: "interrupted"; partial: string; reason: "aborted" | "network" | "ended" };

export interface StreamFailure {
  kind: "api" | "network";
  code: string;
  message: string;
  status: number | null;
  requestId: string | null;
}

/** Turn anything thrown by `fetch` into one describable failure. */
export function toStreamFailure(error: unknown): StreamFailure {
  if (isApiError(error)) {
    return {
      kind: "api",
      code: error.code,
      message: error.message,
      status: error.status,
      requestId: error.requestId,
    };
  }
  // A rejected fetch (offline, DNS, CORS, an aborted request before headers).
  return {
    kind: "network",
    code: error instanceof DOMException && error.name === "AbortError" ? "aborted" : "network",
    message: error instanceof Error ? error.message : "The connection failed.",
    status: null,
    requestId: null,
  };
}

export function parseChatEvent(frame: SseFrame): ChatStreamEvent {
  let payload: unknown;
  try {
    payload = JSON.parse(frame.data);
  } catch {
    return { type: "unknown", event: frame.event };
  }
  if (typeof payload !== "object" || payload === null) {
    return { type: "unknown", event: frame.event };
  }
  const data = payload as Record<string, unknown>;

  switch (frame.event) {
    case "token":
      return { type: "token", text: typeof data.text === "string" ? data.text : "" };
    case "final": {
      const metadata = data.metadata;
      if (typeof data.reply !== "string" || typeof metadata !== "object" || metadata === null) {
        return { type: "unknown", event: frame.event };
      }
      return {
        type: "final",
        reply: data.reply,
        replaced: data.replaced === true,
        sessionId: typeof data.session_id === "string" ? data.session_id : "",
        messageId: typeof data.message_id === "string" ? data.message_id : null,
        metadata: metadata as unknown as ChatMetadata,
      };
    }
    case "error":
      return {
        type: "error",
        code: typeof data.code === "string" ? data.code : "internal_error",
        message:
          typeof data.message === "string" ? data.message : "Something went wrong. Try again.",
      };
    default:
      return { type: "unknown", event: frame.event };
  }
}

/**
 * Send one message and read its reply as it arrives.
 *
 * Resolves for anything that happens *after* the response started; a failure
 * before the first byte (401, 403, 404, 422, 429, 503) throws, and the caller
 * turns it into a plain-language message with {@link toStreamFailure}.
 */
export async function streamChatMessage(input: StreamChatInput): Promise<StreamOutcome> {
  const parser = createSseParser();
  let partial = "";
  let outcome: StreamOutcome | null = null;

  const response = await input.client.postForStream(
    CHAT_ENDPOINTS.stream(input.sessionId),
    { message: input.message, locale: input.locale },
    { signal: input.signal },
  );

  const body = response.body;
  if (body === null) {
    return { kind: "interrupted", partial, reason: "ended" };
  }

  const reader = body.getReader();
  const decoder = new TextDecoder();

  const handle = (frames: SseFrame[]): void => {
    for (const frame of frames) {
      const event = parseChatEvent(frame);
      if (event.type === "token") {
        partial += event.text;
      } else if (event.type === "final") {
        outcome = {
          kind: "final",
          reply: event.reply,
          replaced: event.replaced,
          metadata: event.metadata,
        };
      } else if (event.type === "error") {
        outcome = { kind: "stream-error", code: event.code, message: event.message };
      }
      // `unknown` is ignored: a newer server may add events this build lacks.
      input.onEvent(event);
    }
  };

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) {
        break;
      }
      handle(parser.push(decoder.decode(value, { stream: true })));
      if (outcome !== null) {
        // `final`/`error` are terminal; stop reading and let the server close.
        break;
      }
    }
    handle(parser.push(decoder.decode()));
    handle(parser.flush());
  } catch (error) {
    const aborted =
      input.signal?.aborted === true ||
      (error instanceof DOMException && error.name === "AbortError");
    return { kind: "interrupted", partial, reason: aborted ? "aborted" : "network" };
  } finally {
    // Always release the socket: a leaked reader keeps the connection (and the
    // server's generator) alive until the tab closes.
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }

  if (outcome !== null) {
    return outcome;
  }
  // The body ended with no terminal event: a proxy closed it, or the server
  // died between tokens. Honest about it either way.
  return { kind: "interrupted", partial, reason: "ended" };
}
