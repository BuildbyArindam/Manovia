/**
 * Reading a streamed reply end to end, with only `fetch` replaced.
 *
 * These tests use the real `ApiClient`, the real SSE parser and the real
 * `ReadableStream` reader, so what is being exercised is the code that has to
 * work against a proxy — including the case where the connection dies halfway.
 */

import { describe, expect, it, vi } from "vitest";

import { ApiClient, MemoryTokenStore } from "@/lib/api";
import { createFakeChatServer, finalFrame, normalMetadata, tokenFrame } from "@/test/chatTransport";

import { parseChatEvent, streamChatMessage, toStreamFailure } from "./stream";

function signedInClient(fetchImpl: (input: string, init?: RequestInit) => Promise<Response>) {
  const tokens = new MemoryTokenStore();
  const client = new ApiClient({ fetchImpl, tokens, now: () => 0 });
  client.applySession({ access_token: "access-1", refresh_token: "refresh-1", expires_in: 900 });
  return client;
}

describe("parseChatEvent", () => {
  it("reads a token event", () => {
    expect(parseChatEvent({ event: "token", data: '{"text":"hello"}' })).toEqual({
      type: "token",
      text: "hello",
    });
  });

  it("reads a final event with its metadata", () => {
    const parsed = parseChatEvent({
      event: "final",
      data: JSON.stringify({
        reply: "The answer",
        replaced: true,
        session_id: "s1",
        message_id: null,
        metadata: normalMetadata({ response_type: "crisis" }),
      }),
    });

    expect(parsed).toMatchObject({ type: "final", reply: "The answer", replaced: true });
  });

  it("reads an in-stream error event", () => {
    expect(parseChatEvent({ event: "error", data: '{"code":"boom","message":"Bad"}' })).toEqual({
      type: "error",
      code: "boom",
      message: "Bad",
    });
  });

  it("does not crash on malformed JSON, and says it is unknown", () => {
    expect(parseChatEvent({ event: "token", data: "not json" })).toEqual({
      type: "unknown",
      event: "token",
    });
  });

  it("ignores an event a future server might add", () => {
    expect(parseChatEvent({ event: "heartbeat", data: "{}" })).toEqual({
      type: "unknown",
      event: "heartbeat",
    });
  });
});

describe("streamChatMessage", () => {
  it("delivers tokens in order and then the final reply", async () => {
    const server = createFakeChatServer({
      streamChunks: [
        tokenFrame("I hear "),
        tokenFrame("that."),
        finalFrame({ reply: "I hear that.", metadata: normalMetadata() }),
      ],
    });
    const client = signedInClient(server.fetchImpl);
    const events: string[] = [];

    const outcome = await streamChatMessage({
      sessionId: "s1",
      message: "I feel anxious",
      locale: "en",
      client,
      onEvent: (event) => {
        events.push(event.type);
      },
    });

    expect(events).toEqual(["token", "token", "final"]);
    expect(outcome.kind).toBe("final");
    if (outcome.kind === "final") {
      expect(outcome.reply).toBe("I hear that.");
      expect(outcome.metadata.response_type).toBe("normal");
    }
    // The message went out as a POST body, never in the URL.
    expect(server.calls.at(-1)?.method).toBe("POST");
    expect(server.calls.at(-1)?.url).not.toContain("I%20feel");
    expect(server.calls.at(-1)?.body).toMatchObject({ message: "I feel anxious", locale: "en" });
    expect(server.calls.at(-1)?.headers.Authorization).toBe("Bearer access-1");
  });

  it("reassembles a frame the network split in half", async () => {
    const whole =
      tokenFrame("one whole sentence") +
      finalFrame({ reply: "one whole sentence", metadata: normalMetadata() });
    const server = createFakeChatServer({
      // Cut the stream at an arbitrary byte, inside a field name.
      streamChunks: [whole.slice(0, 12), whole.slice(12)],
    });

    const outcome = await streamChatMessage({
      sessionId: "s1",
      message: "hi",
      locale: "en",
      client: signedInClient(server.fetchImpl),
      onEvent: () => undefined,
    });

    expect(outcome).toMatchObject({ kind: "final", reply: "one whole sentence" });
  });

  it("keeps the partial text when the connection dies mid-stream", async () => {
    const server = createFakeChatServer({
      streamChunks: [
        tokenFrame("That sounds "),
        // A server that closes the body without a `final` event.
      ],
    });
    const seen: string[] = [];

    const outcome = await streamChatMessage({
      sessionId: "s1",
      message: "hi",
      locale: "en",
      client: signedInClient(server.fetchImpl),
      onEvent: (event) => {
        if (event.type === "token") {
          seen.push(event.text);
        }
      },
    });

    expect(outcome).toEqual({ kind: "interrupted", partial: "That sounds ", reason: "ended" });
    expect(seen).toEqual(["That sounds "]);
  });

  it("reports an abort as an abort, not as a network failure", async () => {
    const server = createFakeChatServer({
      streamChunks: [tokenFrame("start ")],
      hangStream: true,
    });
    const controller = new AbortController();

    const pending = streamChatMessage({
      sessionId: "s1",
      message: "hi",
      locale: "en",
      client: signedInClient(server.fetchImpl),
      signal: controller.signal,
      onEvent: (event) => {
        if (event.type === "token") {
          // Stop reading as soon as anything has arrived.
          controller.abort();
        }
      },
    });

    await expect(pending).resolves.toMatchObject({ kind: "interrupted", reason: "aborted" });
  });

  it("surfaces an in-stream error event instead of a silent stop", async () => {
    const server = createFakeChatServer({
      streamChunks: [
        tokenFrame("half "),
        'event: error\ndata: {"code":"llm_failed","message":"Bad"}\n\n',
      ],
    });

    const outcome = await streamChatMessage({
      sessionId: "s1",
      message: "hi",
      locale: "en",
      client: signedInClient(server.fetchImpl),
      onEvent: () => undefined,
    });

    expect(outcome).toEqual({ kind: "stream-error", code: "llm_failed", message: "Bad" });
  });

  it("throws before the first byte, so a 403 is an HTTP error and not an event", async () => {
    const server = createFakeChatServer({
      streamStatus: 403,
      streamError: { code: "consent_required", message: "Consent required: ai_disclosure" },
    });
    const client = signedInClient(server.fetchImpl);

    await expect(
      streamChatMessage({
        sessionId: "s1",
        message: "hi",
        locale: "en",
        client,
        onEvent: () => undefined,
      }),
    ).rejects.toMatchObject({ name: "ApiError", status: 403, code: "consent_required" });
  });

  it("turns a network-level failure into a describable one", async () => {
    const failure = toStreamFailure(new TypeError("Failed to fetch"));
    expect(failure).toMatchObject({ kind: "network", code: "network", status: null });
  });

  it("cancels the stream instead of leaving the socket open", async () => {
    // The server keeps the connection open (a proxy may), the client has its
    // `final` event: it must stop reading *and* release the socket.
    const cancel = vi.fn();
    const server = createFakeChatServer({
      streamChunks: [tokenFrame("a "), finalFrame({ reply: "a b", metadata: normalMetadata() })],
      hangStream: true,
      onStreamCancel: cancel,
    });

    await streamChatMessage({
      sessionId: "s1",
      message: "hi",
      locale: "en",
      client: signedInClient(server.fetchImpl),
      onEvent: () => undefined,
    });

    expect(cancel).toHaveBeenCalled();
  });
});
