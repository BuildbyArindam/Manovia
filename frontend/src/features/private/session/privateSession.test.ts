/**
 * The Private Mode session, end to end, with the real rules and the real content.
 *
 * The rules engine, escalator and output guard here are built from the committed
 * bundle, not from fixtures: the assertions are about the pipeline (order of
 * operations, what is stored, what the UI is told), and a fixture pipeline could
 * pass while the real one did not.
 *
 * The two assertions that matter most:
 *   - at HIGH and IMMINENT the reply model is never called (`requests` stays empty),
 *     because crisis detection runs before any model and the reply is pre-written;
 *   - `fetch` is never called, for any message, at any level.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PrivateSession, PrivateSessionError, HISTORY_TURNS } from "./privateSession";
import { buildEscalator, plainText } from "../crisis/escalation";
import { compilePatternSet } from "../rules/bundleSchema";
import { buildOutputGuard } from "../rules/outputGuard";
import { RuleEngine } from "../rules/ruleEngine";
import { closePrivateDatabase, openPrivateDatabase } from "../store/idb";
import type { PrivateDatabase } from "../store/idb";
import { SettingsStore, TranscriptStore } from "../store/localStore";
import { TemplateReplyModel } from "../models/templateReply";
import { FakeEmotionModel, FakeSafetyModel, ScriptedReplyModel } from "../testSupport/modelFakes";
import { readBundle } from "../testSupport/repoArtifacts";
import type { ReplyModel } from "../models/types";

const PASSPHRASE = "the monsoon reached kolkata early this year";
const CONVERSATION = "conv-private-session";

const bundle = readBundle();

const patterns = compilePatternSet(bundle);
const engine = new RuleEngine(patterns);
const escalator = buildEscalator(bundle);
const guard = buildOutputGuard(bundle, patterns);

let database: PrivateDatabase;
let settings: SettingsStore;
let transcript: TranscriptStore;
let replies: ScriptedReplyModel;
let emotion: FakeEmotionModel;
let safety: FakeSafetyModel;
let fetchSpy: ReturnType<typeof vi.fn>;

const GOOD_REPLY = "That sounds like a lot to carry. What has today been like for you?";

interface Rig {
  session: PrivateSession;
  template: TemplateReplyModel;
}

async function rig(
  options: {
    region?: string | null;
    storeTranscript?: boolean;
    replies?: ReplyModel | null;
    raiseOnCaution?: boolean;
  } = {},
): Promise<Rig> {
  await settings.save({
    privateMode: true,
    region: options.region ?? "IN",
    locale: "en",
    storeTranscript: options.storeTranscript ?? true,
  });
  const template = new TemplateReplyModel({ bundle, guard });
  await template.init();
  const session = new PrivateSession({
    engine,
    escalator,
    guard,
    replies: options.replies ?? replies,
    fallbackReplies: template,
    emotion,
    safety,
    transcript,
    settings,
    conversationId: CONVERSATION,
    raiseOnCaution: options.raiseOnCaution,
    isoNow: () => "2026-10-16T22:20:00.000Z",
    monotonic: (() => {
      let tick = 0;
      return () => (tick += 1);
    })(),
  });
  return { session, template };
}

beforeEach(async () => {
  closePrivateDatabase();
  await new Promise<void>((resolve) => {
    const request = indexedDB.deleteDatabase("manovia-private");
    request.onsuccess = () => resolve();
    request.onerror = () => resolve();
    request.onblocked = () => resolve();
  });
  database = await openPrivateDatabase();
  settings = new SettingsStore(database);
  transcript = new TranscriptStore(database, settings);
  await transcript.createKey(PASSPHRASE);
  replies = new ScriptedReplyModel([GOOD_REPLY]);
  emotion = new FakeEmotionModel();
  safety = new FakeSafetyModel();
  fetchSpy = vi.fn(() => Promise.reject(new Error("network use in Private Mode")));
  vi.stubGlobal("fetch", fetchSpy);
});

afterEach(async () => {
  transcript.lock();
  vi.unstubAllGlobals();
  await closePrivateDatabase();
});

describe("a normal message", () => {
  it("answers with the model's reply and says the model wrote it", async () => {
    const { session } = await rig();
    const turn = await session.send("work has been stressful this week");

    expect(turn.reply).toBe(GOOD_REPLY);
    expect(turn.metadata.response_type).toBe("normal");
    expect(turn.metadata.risk_level).toBe("none");
    expect(turn.metadata.degraded).toBe(false);
    expect(turn.metadata.persisted).toBe(true);
    expect(turn.metadata.region).toBe("IN");
    expect(turn.metadata.locale).toBe("en");
    expect(turn.diagnostics.guard?.passed).toBe(true);
    expect(turn.diagnostics.runtimeLabel).toContain("scripted test double");
    expect(turn.diagnostics.warnings).toEqual([]);
    expect(turn.diagnostics.assessedInMs).toBeGreaterThanOrEqual(0);
    // The reply model was given the message and the level, and nothing else that
    // could bias it: no server, no prompt injection, no retrieved documents.
    expect(replies.requests).toHaveLength(1);
    expect(replies.requests[0]?.text).toBe("work has been stressful this week");
    expect(replies.requests[0]?.level).toBe("none");
  });

  it("never touches the network, whatever the message says", async () => {
    const { session } = await rig();
    await session.send("hello");
    await session.send("I want to kill myself tonight");
    await session.send("my friend says she will end it all");
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("labels the emotion when the classifier is there, and carries on when it is not", async () => {
    const { session } = await rig();
    const withEmotion = await session.send("I feel so sad and alone");
    expect(withEmotion.metadata.emotion).toBe("sadness");
    expect(withEmotion.diagnostics.emotion?.source).toBe("emotion");

    // The model is already loaded, so the failure has to happen on the call itself —
    // which is the realistic case: a WASM compile that dies mid-inference.
    emotion.classify = async () => {
      throw new Error("wasm compile failed");
    };
    const without = await session.send("I feel so sad and alone");
    expect(without.metadata.emotion).toBeNull();
    expect(without.diagnostics.warnings.join()).toMatch(/emotion classifier unavailable/);
    expect(without.reply).toBe(GOOD_REPLY);
  });

  it("keeps a bounded history for the next prompt", async () => {
    const { session } = await rig();
    for (let index = 0; index < HISTORY_TURNS + 2; index += 1) {
      await session.send(`message number ${index}`);
    }
    expect(session.turns()).toHaveLength(HISTORY_TURNS * 2);
    expect(replies.requests.at(-1)?.history).toHaveLength(HISTORY_TURNS);
    session.reset();
    expect(session.turns()).toHaveLength(0);
  });

  it("refuses an empty message the way the server does", async () => {
    const { session } = await rig();
    await expect(session.send("   ")).rejects.toThrow(PrivateSessionError);
    await expect(session.send("")).rejects.toMatchObject({ code: "message_empty" });
  });
});

describe("a crisis message", () => {
  it("answers with the pre-written card and never calls a model", async () => {
    const { session } = await rig();
    const turn = await session.send("I have a plan to kill myself tonight and I have the pills");

    expect(turn.metadata.risk_level).toMatch(/^(high|imminent)$/);
    expect(turn.metadata.response_type).toBe("crisis");
    expect(turn.plan.allowLlm).toBe(false);
    // AGENTS.md rule 1: crisis detection runs before any model call. Not "the model
    // was told to be careful" — it was never asked.
    expect(replies.requests).toEqual([]);
    expect(turn.diagnostics.guard).toBeNull();
    expect(turn.diagnostics.runtimeLabel).toMatch(/deterministic crisis copy/);

    const message = turn.metadata.crisis;
    expect(message).not.toBeNull();
    expect(message?.template_id).toMatch(/^crisis\./);
    // The reply text is the card rendered plainly, with the region's number in it.
    expect(turn.reply).toBe(plainText(message as never));
    expect(turn.reply).toContain(turn.plan.emergencyNumber ?? "");
    expect(turn.metadata.emergency?.region).toBe("IN");
    expect(turn.metadata.resources.length).toBeGreaterThan(0);
    expect(turn.metadata.resources[0]?.kind).toBe("emergency");
    expect(turn.metadata.about_someone_else).toBe(false);
    expect(turn.metadata.degraded).toBe(false);
    expect(turn.metadata.persisted).toBe(true);
    expect(turn.diagnostics.stored).toBe(true);
  });

  it("serves the supporter copy when the message is about somebody else", async () => {
    const { session } = await rig();
    // Corpus P-0254: third person, no first-person speaker, HIGH.
    const turn = await session.send("my brother keeps saying he will end it all");
    expect(turn.metadata.about_someone_else).toBe(true);
    expect(turn.metadata.crisis?.template_id).toBe("crisis.about_someone_else");
    expect(replies.requests).toEqual([]);
  });

  it("shows the fallback region's numbers when the region is unknown", async () => {
    const { session } = await rig({ region: "ZZ" });
    const turn = await session.send("I am going to end my life tonight");
    expect(turn.metadata.region).toBe("DEFAULT");
    expect(turn.plan.regionFallbackUsed).toBe(true);
    expect(turn.metadata.emergency?.number).toBeTruthy();
  });

  it("stores a crisis turn under the stored tier, encrypted", async () => {
    const { session } = await rig();
    await session.send("I have a plan to kill myself tonight");
    const turns = await transcript.list(CONVERSATION);
    expect(turns).toHaveLength(2);
    expect(turns[0]?.payload.role).toBe("user");
    expect(turns[0]?.payload.storedLevel).toBe("crisis");
    expect(turns[0]?.payload.crisis).toBe(true);
    expect(turns[1]?.payload.role).toBe("assistant");
    // And nothing readable is in the database.
    const raw = JSON.stringify(await database.all<unknown>("messages"));
    expect(raw).not.toContain("kill myself");
    expect(raw).not.toContain("crisis");
  });
});

describe("a medium-risk message", () => {
  it("appends the reviewed check-in line after a model reply", async () => {
    const { session } = await rig();
    // Corpus P-0187: the rules engine reads this as MEDIUM.
    const turn = await session.send("nothing matters any more");
    expect(turn.metadata.risk_level).toBe("medium");
    expect(turn.metadata.response_type).toBe("check_in");
    const checkIn = turn.metadata.check_in;
    expect(checkIn).not.toBeNull();
    const suffix = checkIn?.body.at(-1) ?? "";
    expect(turn.reply.endsWith(suffix)).toBe(true);
    expect(turn.reply.startsWith(GOOD_REPLY)).toBe(true);
    expect(turn.diagnostics.guard?.passed).toBe(true);
  });

  it("does not repeat the check-in line when a template wrote the reply", async () => {
    const template = new TemplateReplyModel({ bundle, guard });
    await template.init();
    const { session } = await rig({ replies: template });
    const turn = await session.send("nothing matters any more");
    expect(turn.metadata.response_type).toBe("check_in");
    expect(turn.diagnostics.degraded).toBe(true);
    const suffix = turn.metadata.check_in?.body.at(-1) ?? "";
    // Present exactly once: the template writer includes it itself.
    expect(turn.reply.split(suffix)).toHaveLength(2);
  });
});

describe("when the guard refuses a reply", () => {
  it("shows the pre-written safe reply and says the reply was replaced", async () => {
    replies.script = [
      "You clearly have clinical depression and should stop taking your medication.",
    ];
    const { session } = await rig();
    const turn = await session.send("I have been feeling awful");

    expect(turn.reply).toBe(bundle.output_guard.safe_replies.en);
    expect(turn.metadata.response_type).toBe("fallback");
    expect(turn.metadata.degraded).toBe(true);
    expect(turn.diagnostics.guard?.passed).toBe(false);
    expect(turn.diagnostics.guard?.categories.join()).toMatch(/diagnosis|medication/);
    expect(turn.diagnostics.runtimeLabel).toMatch(/output guard/);
    expect(turn.diagnostics.warnings.join()).toMatch(/did not pass the output guard/);
    // The refused text is not what got stored, either.
    const turns = await transcript.list(CONVERSATION);
    expect(turns[1]?.payload.text).toBe(bundle.output_guard.safe_replies.en);
  });

  it("insists on a check-in at MEDIUM even when the model forgot one", async () => {
    replies.script = ["Breathing slowly can help with that feeling."];
    const { session } = await rig();
    const turn = await session.send("nothing matters any more");
    expect(turn.diagnostics.guard?.categories).toContain("missing_check_in");
    expect(turn.metadata.response_type).toBe("fallback");
    // The safe reply, then the MEDIUM check-in line the server always appends after
    // a guarded reply — a refused reply still ends by asking how the person is.
    const safe = bundle.output_guard.safe_replies.en ?? "";
    const suffix = turn.metadata.check_in?.body.at(-1) ?? "";
    expect(turn.reply.startsWith(safe)).toBe(true);
    expect(turn.reply.endsWith(suffix)).toBe(true);
    expect(suffix.length).toBeGreaterThan(10);
  });
});

describe("when a runtime fails", () => {
  it("falls back to the template writer instead of leaving nobody answering", async () => {
    replies.failWith = new Error("WebGPU device lost");
    const { session } = await rig();
    const turn = await session.send("work has been stressful");
    expect(turn.metadata.degraded).toBe(true);
    expect(turn.diagnostics.runtimeLabel).toMatch(/Template replies/);
    expect(turn.reply.trim().length).toBeGreaterThan(20);
    expect(turn.diagnostics.guard?.passed).toBe(true);
  });

  it("raises a NONE turn to MEDIUM when the safety classifier asks for caution", async () => {
    const { session } = await rig();
    // The rules engine reads this as an ordinary hard day; the classifier disagrees.
    expect(engine.assess("work has been stressful this week").level).toBe("none");
    safety.classify = async () => ({
      label: "crisis-adjacent",
      score: 0.9,
      raises_caution: true,
      source: "safety",
    });
    const turn = await session.send("work has been stressful this week");
    expect(turn.assessment.level).toBe("medium");
    expect(turn.diagnostics.raisedByModel).toBe(true);
    expect(turn.diagnostics.warnings.join()).toMatch(/more caution/);
    expect(turn.metadata.risk_level).toBe("medium");
  });

  it("never lets a classifier lower a level the rules engine raised", async () => {
    const { session } = await rig();
    safety.classify = async () => ({
      label: "no-signal",
      score: 0.01,
      raises_caution: false,
      source: "safety",
    });
    const turn = await session.send("I have a plan to kill myself tonight and I have the pills");
    expect(turn.metadata.risk_level).toMatch(/^(high|imminent)$/);
    expect(turn.diagnostics.raisedByModel).toBe(false);
    expect(turn.metadata.response_type).toBe("crisis");
    // The level stands, so the model is still never called.
    expect(replies.requests).toEqual([]);
  });

  it("can be told not to let a classifier change the level at all", async () => {
    const { session } = await rig({ raiseOnCaution: false });
    safety.classify = async () => ({
      label: "crisis-adjacent",
      score: 0.9,
      raises_caution: true,
      source: "safety",
    });
    const turn = await session.send("work has been stressful this week");
    expect(turn.diagnostics.safetySignal?.raises_caution).toBe(true);
    expect(turn.diagnostics.raisedByModel).toBe(false);
    expect(turn.assessment.level).toBe("none");
  });

  it("reports a classifier that will not load as a warning, not as a failure", async () => {
    safety.classify = async () => {
      throw new Error("model file missing");
    };
    const { session } = await rig();
    const turn = await session.send("a hard day");
    expect(turn.diagnostics.warnings.join()).toMatch(/safety classifier unavailable/);
    expect(turn.reply).toBe(GOOD_REPLY);
  });

  it("answers anyway when nothing can be stored", async () => {
    const { session } = await rig({ storeTranscript: false });
    const turn = await session.send("I have a plan to kill myself tonight");
    expect(turn.metadata.persisted).toBe(false);
    expect(turn.diagnostics.stored).toBe(false);
    expect(turn.metadata.response_type).toBe("crisis");
    expect(await database.count("messages")).toBe(0);
  });

  it("answers anyway when the transcript is locked", async () => {
    const { session } = await rig();
    transcript.lock();
    const turn = await session.send("a hard day");
    expect(turn.metadata.persisted).toBe(false);
    expect(turn.reply).toBe(GOOD_REPLY);
  });
});
