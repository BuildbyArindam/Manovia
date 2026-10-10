/**
 * A fake chat transport for the component tests.
 *
 * The tests drive the real `ApiClient`, the real SSE parser and the real
 * `ReadableStream` reader — only `fetch` is replaced. That matters: a test that
 * faked the stream reader would be asserting the fake's behaviour, not the
 * code that has to survive a proxy that batches twelve frames into one read.
 *
 * Chunks are delivered exactly as written, so a test can put a frame boundary
 * in the middle of a word and prove the parser copes.
 */

import type { FetchImpl } from "@/lib/api";
import type { ChatSession } from "@/features/chat/types";

export interface RecordedCall {
  url: string;
  method: string;
  body: unknown;
  headers: Record<string, string>;
}

export interface FakeChatServerOptions {
  /** Status for `POST /chat/sessions` (default 201). */
  sessionStatus?: number;
  /** Error envelope for a failing session request (used when the status is 4xx). */
  sessionError?: { code: string; message: string; request_id?: string };
  /** Body for `POST /chat/sessions` (default a real-shaped ephemeral session). */
  sessionBody?: Partial<ChatSession>;
  /** Status for the stream request (default 200). */
  streamStatus?: number;
  /** Error envelope body when the stream status is not 2xx. */
  streamError?: { code: string; message: string; request_id?: string };
  /**
   * Raw chunks the stream body delivers, in order. Strings are encoded as UTF-8;
   * a function receives the controller so a test can end the stream badly.
   */
  streamChunks?: ReadonlyArray<
    string | ((controller: ReadableStreamDefaultController<Uint8Array>) => void)
  >;
  /** Never close the body: simulates a server that holds the connection open. */
  hangStream?: boolean;
  /** Called if the client cancels the stream (releases the socket). */
  onStreamCancel?: () => void;
  /** Fail the session request at the network level (no HTTP response at all). */
  networkFailure?: "session" | "stream" | "both";
}

export interface FakeChatServer {
  fetchImpl: FetchImpl;
  calls: RecordedCall[];
}

export const DEFAULT_SESSION_ID = "11111111-2222-3333-4444-555555555555";

export function sessionPayload(overrides: Partial<ChatSession> = {}): ChatSession {
  return {
    id: DEFAULT_SESSION_ID,
    user_id: "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    created_at: "2026-10-10T10:00:00Z",
    ended_at: null,
    persistent: false,
    expires_at: "2026-10-10T10:30:00Z",
    ...overrides,
  };
}

/** One SSE frame, exactly as the backend writes it (single-line JSON). */
export function frame(event: string, data: unknown): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

export function tokenFrame(text: string): string {
  return frame("token", { text });
}

export function finalFrame(payload: {
  reply: string;
  replaced?: boolean;
  metadata: Record<string, unknown>;
  session_id?: string;
  message_id?: string | null;
}): string {
  return frame("final", {
    reply: payload.reply,
    replaced: payload.replaced ?? false,
    session_id: payload.session_id ?? DEFAULT_SESSION_ID,
    message_id: payload.message_id ?? null,
    metadata: payload.metadata,
  });
}

export function createFakeChatServer(options: FakeChatServerOptions = {}): FakeChatServer {
  const calls: RecordedCall[] = [];
  const encoder = new TextEncoder();

  const fetchImpl: FetchImpl = async (url, init) => {
    const method = init?.method ?? "GET";
    const rawBody = init?.body;
    calls.push({
      url,
      method,
      body: typeof rawBody === "string" && rawBody !== "" ? JSON.parse(rawBody) : undefined,
      headers: (init?.headers ?? {}) as Record<string, string>,
    });

    const path = new URL(url, "http://localhost").pathname;

    if (path.endsWith("/chat/sessions")) {
      if (options.networkFailure === "session" || options.networkFailure === "both") {
        throw new TypeError("Failed to fetch");
      }
      const status = options.sessionStatus ?? 201;
      if (status >= 400) {
        return jsonResponse(
          status,
          {
            error: {
              code: options.sessionError?.code ?? "internal_error",
              message: options.sessionError?.message ?? "Something went wrong.",
              request_id: options.sessionError?.request_id ?? "req-session-1",
            },
          },
          { "X-Request-ID": options.sessionError?.request_id ?? "req-session-1" },
        );
      }
      return jsonResponse(status, sessionPayload(options.sessionBody));
    }

    if (path.includes("/stream")) {
      if (options.networkFailure === "stream" || options.networkFailure === "both") {
        throw new TypeError("Failed to fetch");
      }
      const status = options.streamStatus ?? 200;
      if (status >= 400) {
        return jsonResponse(
          status,
          {
            error: {
              code: options.streamError?.code ?? "internal_error",
              message: options.streamError?.message ?? "Something went wrong.",
              request_id: options.streamError?.request_id ?? "req-test-1",
            },
          },
          { "X-Request-ID": options.streamError?.request_id ?? "req-test-1" },
        );
      }

      const chunks = options.streamChunks ?? [];
      const hang = options.hangStream ?? false;
      const signal = init?.signal ?? null;
      const body = new ReadableStream<Uint8Array>({
        cancel() {
          options.onStreamCancel?.();
        },
        start(controller) {
          // Honour the abort signal the way `fetch` does: the body stream
          // errors with an AbortError, and a reader waiting on it rejects.
          signal?.addEventListener("abort", () => {
            try {
              controller.error(new DOMException("The user aborted a request.", "AbortError"));
            } catch {
              // Already closed or errored: nothing left to do.
            }
          });
          for (const chunk of chunks) {
            if (typeof chunk === "string") {
              controller.enqueue(encoder.encode(chunk));
            } else {
              chunk(controller);
            }
          }
          if (!hang) {
            controller.close();
          }
        },
      });

      return new Response(body, {
        status: 200,
        headers: { "Content-Type": "text/event-stream", "X-Accel-Buffering": "no" },
      });
    }

    // Anything else the page asks for (helplines, consent documents): an empty
    // success, so a component test never depends on an endpoint it is not about.
    return jsonResponse(200, {});
  };

  return { fetchImpl, calls };
}

function jsonResponse(
  status: number,
  body: unknown,
  headers: Record<string, string> = {},
): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

/** Metadata for an ordinary, low-risk reply. */
export function normalMetadata(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    risk_level: "none",
    emotion: "neutral",
    response_type: "normal",
    resources: [],
    emergency: null,
    crisis: null,
    check_in: null,
    degraded: false,
    persisted: false,
    about_someone_else: false,
    locale: "en",
    region: "DEFAULT",
    ...overrides,
  };
}
