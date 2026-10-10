/**
 * The shipped stream client against a real backend — opt-in, skipped by default.
 *
 * Why this file exists: the Day 12 e2e is Playwright, and a sandbox without a
 * browser binary cannot run it. What a browser adds on top of this file is
 * rendering, layout and real network buffering; everything *below* the render —
 * authentication, consent, session creation, the SSE read loop, the crisis
 * metadata — is exercised here by the same `streamChatMessage` the browser
 * calls, against a real `uvicorn` with `LLM_PROVIDER=fake`.
 *
 * It is skipped unless `MANOVIA_LIVE_API` names a running stack, so the suite
 * still passes fully offline (AGENTS.md):
 *
 *   MANOVIA_LIVE_API=http://127.0.0.1:5173 npx vitest run liveBackend
 *
 * Expected backend settings: `LLM_PROVIDER=fake`, `SAFETY_ML_ENABLED=false`
 * (see PROGRESS.md → Known issues → Day 11 chat notes).
 */

import { beforeAll, describe, expect, it } from "vitest";

import { ApiClient, MemoryTokenStore } from "@/lib/api";
import {
  createGuestSession,
  fetchConsentRequirements,
  recordConsents,
  REQUIRED_CONSENTS,
} from "@/lib/endpoints";

import { streamChatMessage } from "./stream";
import type { ChatStreamEvent } from "./types";

const LIVE = process.env.MANOVIA_LIVE_API;
const describeLive = LIVE === undefined || LIVE === "" ? describe.skip : describe;

/** The Fake provider's unscripted reply — proof of what the model would say. */
const FAKE_LLM_MARKER = "What has today been like for you?";
const HIGH_RISK = "I want to kill myself";
const ORDINARY = "I had a bad day and I cannot switch my brain off";

describeLive("the shipped chat client against a live backend", () => {
  let client: ApiClient;
  let sessionId: string;

  beforeAll(async () => {
    const base = (LIVE as string).replace(/\/+$/, "");
    client = new ApiClient({ baseUrl: `${base}/api/v1`, tokens: new MemoryTokenStore() });

    // Guest onboarding, exactly as the browser does it.
    const session = await createGuestSession(client);
    expect(session.user.is_anonymous).toBe(true);

    const requirements = await fetchConsentRequirements(client);
    const versions = new Map(requirements.documents.map((doc) => [doc.kind, doc.version]));
    await recordConsents(
      client,
      REQUIRED_CONSENTS.map((kind) => ({
        kind,
        version: versions.get(kind) ?? "2026-10-01",
        granted: true,
      })),
    );

    const chat = await client.post<{ id: string }>("/chat/sessions", {});
    sessionId = chat.id;
  }, 30_000);

  it("streams an ordinary reply: tokens, then an authoritative final", async () => {
    const events: ChatStreamEvent[] = [];

    const outcome = await streamChatMessage({
      sessionId,
      message: ORDINARY,
      locale: "en",
      client,
      onEvent: (event) => {
        events.push(event);
      },
    });

    const tokens = events.filter((event) => event.type === "token");
    expect(tokens.length).toBeGreaterThan(0);
    expect(outcome.kind).toBe("final");
    if (outcome.kind !== "final") {
      return;
    }
    // The model answered, so the Fake's reply is what we expect to see.
    expect(outcome.reply).toContain(FAKE_LLM_MARKER);
    expect(outcome.metadata.response_type).toBe("normal");
    expect(outcome.metadata.risk_level).toBe("none");
    expect(outcome.metadata.crisis).toBeNull();
    expect(outcome.replaced).toBe(false);
  }, 30_000);

  it("answers a HIGH-risk message with the crisis card and never the model", async () => {
    const events: ChatStreamEvent[] = [];

    const outcome = await streamChatMessage({
      sessionId,
      message: HIGH_RISK,
      locale: "en",
      client,
      onEvent: (event) => {
        events.push(event);
      },
    });

    expect(outcome.kind).toBe("final");
    if (outcome.kind !== "final") {
      return;
    }
    expect(outcome.metadata.response_type).toBe("crisis");
    expect(outcome.metadata.risk_level).toBe("high");
    // The CrisisCard's inputs are all present.
    expect(outcome.metadata.crisis).not.toBeNull();
    expect(outcome.metadata.crisis?.template_id).toBe("crisis.high");
    expect(outcome.metadata.resources.length).toBeGreaterThan(0);
    // No emotion is claimed on a crisis turn, and the model was not called:
    // the Fake's sentence appears nowhere in the reply or in the stream.
    expect(outcome.metadata.emotion).toBeNull();
    expect(outcome.reply).not.toContain(FAKE_LLM_MARKER);
    const streamed = events
      .filter((event): event is Extract<ChatStreamEvent, { type: "token" }> => event.type === "token")
      .map((event) => event.text)
      .join("");
    expect(streamed).not.toContain(FAKE_LLM_MARKER);
  }, 30_000);

  it("keeps the partial reply when the client drops the connection mid-stream", async () => {
    const controller = new AbortController();
    let firstToken = "";

    const outcome = await streamChatMessage({
      sessionId,
      message: "Tell me something longer so there is more than one token to read.",
      locale: "en",
      client,
      signal: controller.signal,
      onEvent: (event) => {
        if (event.type === "token" && firstToken === "") {
          firstToken = event.text;
          // The reader bails out as soon as anything has arrived: exactly what
          // a closed tab or a dropped network does to the loop.
          controller.abort();
        }
      },
    });

    expect(outcome.kind).toBe("interrupted");
    if (outcome.kind !== "interrupted") {
      return;
    }
    expect(outcome.reason).toBe("aborted");
    expect(outcome.partial.startsWith(firstToken)).toBe(true);
  }, 30_000);
});
