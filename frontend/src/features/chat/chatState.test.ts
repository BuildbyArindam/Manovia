/**
 * The conversation reducer: streaming, crisis, interruption and recovery,
 * tested as the pure function it is.
 */

import { describe, expect, it } from "vitest";

import { crisisMessage } from "@/test/crisisFixtures";

import {
  chatReducer,
  failureFromStreamFailure,
  initialChatState,
  isSoftened,
  latestAssistantTurn,
} from "./chatState";
import type { ChatMetadata } from "./types";

function metadata(overrides: Partial<ChatMetadata> = {}): ChatMetadata {
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

describe("chatReducer", () => {
  it("starts empty, ephemeral, English, saving off", () => {
    const state = initialChatState();

    expect(state.turns).toEqual([]);
    expect(state.sessionId).toBeNull();
    expect(state.persistent).toBe(false);
    expect(state.saveHistory).toBe(false);
    expect(state.locale).toBe("en");
  });

  it("appends the user message and an empty streaming placeholder on send", () => {
    const state = chatReducer(initialChatState(), {
      type: "send-start",
      id: "u1",
      text: "I feel anxious",
      assistantId: "a1",
    });

    expect(state.turns).toHaveLength(2);
    expect(state.turns[0]).toMatchObject({ role: "user", text: "I feel anxious" });
    expect(state.turns[1]).toMatchObject({ role: "assistant", text: "", streaming: true });
    expect(state.sending).toBe(true);
  });

  it("appends tokens in order", () => {
    let state = chatReducer(initialChatState(), {
      type: "send-start",
      id: "u1",
      text: "hi",
      assistantId: "a1",
    });
    state = chatReducer(state, { type: "token", id: "a1", text: "I hear " });
    state = chatReducer(state, { type: "token", id: "a1", text: "that." });

    expect(latestAssistantTurn(state.turns)?.text).toBe("I hear that.");
  });

  it("replaces the streamed text when final says it was replaced", () => {
    let state = chatReducer(initialChatState(), {
      type: "send-start",
      id: "u1",
      text: "hi",
      assistantId: "a1",
    });
    state = chatReducer(state, { type: "token", id: "a1", text: "half a sent" });
    state = chatReducer(state, {
      type: "final",
      id: "a1",
      reply: "The whole, checked answer.",
      replaced: true,
      metadata: metadata(),
    });

    const turn = latestAssistantTurn(state.turns);
    expect(turn?.text).toBe("The whole, checked answer.");
    expect(turn?.replaced).toBe(true);
    expect(turn?.streaming).toBe(false);
    expect(state.sending).toBe(false);
  });

  it("keeps the words that arrived when the stream is cut off, and says so", () => {
    let state = chatReducer(initialChatState(), {
      type: "send-start",
      id: "u1",
      text: "hi",
      assistantId: "a1",
    });
    state = chatReducer(state, { type: "token", id: "a1", text: "That sounds " });
    state = chatReducer(state, { type: "interrupted", id: "a1", reason: "network" });

    const turn = latestAssistantTurn(state.turns);
    expect(turn?.text).toBe("That sounds ");
    expect(turn?.incomplete).toBe(true);
    expect(state.failure?.kind).toBe("stream-interrupted");
    expect(state.sending).toBe(false);
  });

  it("drops the placeholder silently when the caller aborted the stream", () => {
    let state = chatReducer(initialChatState(), {
      type: "send-start",
      id: "u1",
      text: "hi",
      assistantId: "a1",
    });
    state = chatReducer(state, { type: "interrupted", id: "a1", reason: "aborted" });

    // The user's message stays: they did send it.
    expect(state.turns).toHaveLength(1);
    expect(state.turns[0]?.role).toBe("user");
    expect(state.failure).toBeNull();
  });

  it("clears the screen but keeps the session", () => {
    let state = chatReducer(initialChatState(), {
      type: "session-created",
      sessionId: "s1",
      persistent: false,
    });
    state = chatReducer(state, { type: "send-start", id: "u1", text: "hi", assistantId: "a1" });
    state = chatReducer(state, { type: "clear-conversation" });

    expect(state.turns).toEqual([]);
    expect(state.sessionId).toBe("s1");
  });

  it("drops the session on a new chat, so the next message opens a fresh one", () => {
    let state = chatReducer(initialChatState(), {
      type: "session-created",
      sessionId: "s1",
      persistent: true,
    });
    state = chatReducer(state, { type: "new-chat" });

    expect(state.sessionId).toBeNull();
    expect(state.persistent).toBe(false);
    expect(state.turns).toEqual([]);
  });

  it("records the save toggle and the language", () => {
    let state = chatReducer(initialChatState(), { type: "set-save-history", value: true });
    expect(state.saveHistory).toBe(true);

    state = chatReducer(state, { type: "set-locale", value: "bn" });
    expect(state.locale).toBe("bn");
  });

  it("does not claim an existing session changed when the save toggle moves", () => {
    let state = chatReducer(initialChatState(), {
      type: "session-created",
      sessionId: "s1",
      persistent: false,
    });
    state = chatReducer(state, { type: "set-save-history", value: true });

    // The toggle moves; the session that already exists is still memory-only.
    expect(state.saveHistory).toBe(true);
    expect(state.persistent).toBe(false);
  });
});

describe("isSoftened", () => {
  const turn = (meta: ChatMetadata | null) => ({
    id: "a1",
    role: "assistant" as const,
    text: "x",
    streaming: false,
    replaced: false,
    incomplete: false,
    metadata: meta,
  });

  it("softens on a crisis reply", () => {
    expect(
      isSoftened([
        turn(metadata({ response_type: "crisis", risk_level: "high", crisis: crisisMessage() })),
      ]),
    ).toBe(true);
  });

  it("softens on an imminent risk even if the response type is not crisis", () => {
    expect(isSoftened([turn(metadata({ risk_level: "imminent" }))])).toBe(true);
  });

  it("does not soften on an ordinary reply", () => {
    expect(isSoftened([turn(metadata())])).toBe(false);
  });

  it("does not soften before the first reply", () => {
    expect(isSoftened([])).toBe(false);
    expect(isSoftened([turn(null)])).toBe(false);
  });
});

describe("failureFromStreamFailure", () => {
  it("says you are offline when the browser says so", () => {
    const failure = failureFromStreamFailure(
      {
        kind: "network",
        code: "network",
        message: "Failed to fetch",
        status: null,
        requestId: null,
      },
      "I feel anxious",
      true,
    );

    expect(failure.kind).toBe("offline");
    expect(failure.retryMessage).toBe("I feel anxious");
  });

  it("says the connection dropped when the browser thinks it is online", () => {
    const failure = failureFromStreamFailure(
      {
        kind: "network",
        code: "network",
        message: "Failed to fetch",
        status: null,
        requestId: null,
      },
      "hi",
      false,
    );

    expect(failure.kind).toBe("network");
  });

  it("maps a missing consent to the agreement, not to a generic error", () => {
    const failure = failureFromStreamFailure(
      {
        kind: "api",
        code: "consent_required",
        message: "Consent required: store_chat",
        status: 403,
        requestId: "req-1",
      },
      null,
      false,
    );

    expect(failure.kind).toBe("consent");
    expect(failure.requestId).toBe("req-1");
  });

  it("prefers the backend's own curated sentence when it has one", () => {
    const failure = failureFromStreamFailure(
      {
        kind: "api",
        code: "message_too_long",
        message: "That message is longer than 4000 characters. Could you send it in smaller parts?",
        status: 422,
        requestId: "req-2",
      },
      "x".repeat(5000),
      false,
    );

    expect(failure.body).toContain("4000 characters");
    // A too-long message is not resent verbatim by Retry.
    expect(failure.retryMessage).toBeNull();
  });

  it("maps an ended session to starting a new chat", () => {
    const failure = failureFromStreamFailure(
      { kind: "api", code: "session_ended", message: "x", status: 409, requestId: null },
      "hi",
      false,
    );

    expect(failure.kind).toBe("session");
    expect(failure.action).toBe("Start a new chat");
  });
});
