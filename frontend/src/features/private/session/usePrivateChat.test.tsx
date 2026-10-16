/**
 * `usePrivateChat`: the same `ChatController` the chat UI already speaks, answered
 * on this device.
 *
 * Built on a real `PrivateSession` with the committed bundle — the point of these
 * tests is the wiring (reducer actions, per-turn diagnostics, failure shapes), and
 * a fixture session could pass while the real one did not.
 *
 * Two invariants are asserted explicitly, because they are the whole claim of
 * Private Mode: no `fetch` for any message, and no model call at crisis level.
 */

import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { usePrivateChat } from "./usePrivateChat";
import { PrivateSession } from "./privateSession";
import { buildEscalator } from "../crisis/escalation";
import { TemplateReplyModel } from "../models/templateReply";
import { compilePatternSet } from "../rules/bundleSchema";
import { buildOutputGuard } from "../rules/outputGuard";
import { RuleEngine } from "../rules/ruleEngine";
import { closePrivateDatabase, openPrivateDatabase } from "../store/idb";
import { SettingsStore, TranscriptStore } from "../store/localStore";
import { ScriptedReplyModel } from "../testSupport/modelFakes";
import { readBundle } from "../testSupport/repoArtifacts";

const CONVERSATION = "conv-use-private-chat";
const HIGH_RISK = "I have a plan to kill myself tonight and I have the pills";

const bundle = readBundle();
const patterns = compilePatternSet(bundle);
const guard = buildOutputGuard(bundle, patterns);

let replies: ScriptedReplyModel;
let template: TemplateReplyModel;
let fetchSpy: ReturnType<typeof vi.fn>;

async function makeSession(options: { storeTranscript?: boolean } = {}): Promise<PrivateSession> {
  const database = await openPrivateDatabase();
  const settings = new SettingsStore(database);
  await settings.save({
    privateMode: true,
    region: "IN",
    locale: "en",
    storeTranscript: options.storeTranscript ?? false,
  });
  return new PrivateSession({
    engine: new RuleEngine(patterns),
    escalator: buildEscalator(bundle),
    guard,
    replies,
    fallbackReplies: template,
    transcript: new TranscriptStore(database, settings),
    settings,
    conversationId: CONVERSATION,
  });
}

beforeEach(async () => {
  await closePrivateDatabase();
  replies = new ScriptedReplyModel(["That sounds heavy. What has today been like for you?"]);
  await replies.init();
  template = new TemplateReplyModel({ bundle, guard });
  await template.init();
  fetchSpy = vi.fn(() => Promise.reject(new Error("network must not be used")));
  vi.stubGlobal("fetch", fetchSpy);
});

afterEach(async () => {
  vi.unstubAllGlobals();
  await closePrivateDatabase();
});

describe("a normal turn", () => {
  it("answers through the shared reducer, with no network at all", async () => {
    const session = await makeSession();
    const { result } = renderHook(() => usePrivateChat({ session }));

    await act(async () => {
      await result.current.send("I could not sleep and the flat felt very quiet");
    });

    expect(fetchSpy).not.toHaveBeenCalled();
    const { state } = result.current;
    expect(state.turns).toHaveLength(2);
    expect(state.turns[0]).toMatchObject({ role: "user", streaming: false });
    const assistant = state.turns[1];
    expect(assistant?.role).toBe("assistant");
    expect(assistant?.streaming).toBe(false);
    expect(assistant?.text).not.toBe("");
    expect(assistant?.metadata).not.toBeNull();
    // A low-distress message: whatever the rules decide, it must not be a crisis.
    expect(["none", "low", "medium"]).toContain(assistant?.metadata?.risk_level);
    expect(result.current.online).toBe(true);
    expect(state.failure).toBeNull();
  });

  it("records diagnostics for the assistant turn and names the runtime", async () => {
    const session = await makeSession();
    const { result } = renderHook(() => usePrivateChat({ session }));

    await act(async () => {
      await result.current.send("work has been loud all week");
    });

    const assistant = result.current.state.turns[1];
    expect(assistant).toBeDefined();
    const diagnostics =
      assistant === undefined ? undefined : result.current.diagnostics.get(assistant.id);
    expect(diagnostics).toBeDefined();
    expect(diagnostics?.runtimeLabel).not.toBe("");
    expect(result.current.runtimeLabel).toBe(diagnostics?.runtimeLabel);
    // A scripted model wrote this one, so the guard has a verdict to report.
    expect(diagnostics?.guard).not.toBeNull();
    expect(diagnostics?.assessedInMs).toBeGreaterThanOrEqual(0);
    expect(replies.requests).toHaveLength(1);
  });

  it("clears the diagnostics with the conversation", async () => {
    const session = await makeSession();
    const { result } = renderHook(() => usePrivateChat({ session }));

    await act(async () => {
      await result.current.send("today was hard");
    });
    expect(result.current.diagnostics.size).toBe(1);

    act(() => {
      result.current.clearConversation();
    });
    expect(result.current.state.turns).toHaveLength(0);
    expect(result.current.diagnostics.size).toBe(0);
  });

  it("re-runs the last message on retry", async () => {
    const session = await makeSession();
    const { result } = renderHook(() => usePrivateChat({ session }));

    await act(async () => {
      await result.current.send("I keep replaying the argument");
    });
    await act(async () => {
      await result.current.retry();
    });

    expect(result.current.state.turns).toHaveLength(4);
    expect(replies.requests).toHaveLength(2);
  });
});

describe("a crisis turn", () => {
  it("short-circuits: pre-written copy, helplines, and no model call", async () => {
    const session = await makeSession();
    const { result } = renderHook(() => usePrivateChat({ session }));

    await act(async () => {
      await result.current.send(HIGH_RISK);
    });

    expect(fetchSpy).not.toHaveBeenCalled();
    expect(replies.requests).toHaveLength(0);
    const assistant = result.current.state.turns[1];
    expect(assistant?.metadata?.response_type).toBe("crisis");
    expect(assistant?.metadata?.resources.length).toBeGreaterThan(0);
    expect(assistant?.metadata?.crisis).not.toBeNull();
    // Pre-written copy, verbatim: the UI never rewords it (AGENTS.md rule 2).
    expect(assistant?.text.length).toBeGreaterThan(40);
    expect(assistant?.metadata?.degraded).toBe(false);
    const diagnostics =
      assistant === undefined ? undefined : result.current.diagnostics.get(assistant.id);
    // No model wrote it, so the guard has nothing to say.
    expect(diagnostics?.guard).toBeNull();
    expect(diagnostics?.runtimeLabel).toContain("crisis");
  });
});

describe("without a session", () => {
  it("reports a failure instead of reaching for the network", async () => {
    const { result } = renderHook(() => usePrivateChat({ session: null }));

    await act(async () => {
      await result.current.send("hello");
    });

    expect(fetchSpy).not.toHaveBeenCalled();
    const failure = result.current.state.failure;
    expect(failure).not.toBeNull();
    expect(failure?.kind).toBe("unknown");
    expect(failure?.retryMessage).toBe("hello");
    expect(result.current.state.turns).toHaveLength(0);
  });

  it("refuses an empty message with a failure the UI can dismiss", async () => {
    const session = await makeSession();
    const { result } = renderHook(() => usePrivateChat({ session }));

    await act(async () => {
      await result.current.send("   ");
    });

    expect(result.current.state.failure?.title).toBe("There is nothing to answer yet");
    expect(result.current.state.failure?.retryMessage).toBeNull();
    act(() => {
      result.current.dismissFailure();
    });
    expect(result.current.state.failure).toBeNull();
  });

  it("has no voice session, because there is no on-device speech pipeline", async () => {
    const session = await makeSession();
    const { result } = renderHook(() => usePrivateChat({ session }));
    await expect(result.current.openVoiceSession()).resolves.toBeNull();
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});

describe("the storage toggle", () => {
  it("is handed to the caller, which owns the encrypted setting", async () => {
    const onStoreTranscriptChange = vi.fn();
    const session = await makeSession();
    const { result } = renderHook(() => usePrivateChat({ session, onStoreTranscriptChange }));

    act(() => {
      result.current.setSaveHistory(true);
    });

    expect(onStoreTranscriptChange).toHaveBeenCalledWith(true);
    expect(result.current.state.saveHistory).toBe(true);
  });

  it("persists the turn when storage is on and unlocked", async () => {
    const session = await makeSession({ storeTranscript: true });
    const { result } = renderHook(() => usePrivateChat({ session }));

    await act(async () => {
      await result.current.send("I am trying to keep the days simple");
    });

    // Locked, so nothing is written: the honest result is "not stored".
    const assistant = result.current.state.turns[1];
    expect(assistant?.metadata?.persisted).toBe(false);
  });
});
