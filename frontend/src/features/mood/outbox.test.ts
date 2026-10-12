/**
 * The offline check-in queue (Day 25): what is stored, what is sent, and what
 * happens on every kind of failure.
 */

import { afterEach, describe, expect, it } from "vitest";

import { ApiError, NetworkUnavailableError, type ApiClient } from "@/lib/api";
import { moodEntry, moodStreak } from "@/test/moodFakes";

import {
  MOOD_OUTBOX_KEY,
  OUTBOX_MAX_AGE_MS,
  OUTBOX_MAX_ITEMS,
  __resetOutboxSyncForTests,
  clearOutbox,
  enqueueCheckIn,
  loadOutbox,
  removeFromOutbox,
  subscribeOutbox,
  syncOutbox,
} from "./outbox";
import type { MoodCheckInResponse } from "./types";

const PAYLOAD = {
  valence: 2,
  energy: 3,
  emotions: ["sadness"],
  factors: ["sleep"],
  note: "Rough night.",
};
const NOW = Date.parse("2026-10-12T09:00:00Z");

function queue(clientId: string, minutesAgo = 5, userId: string | null = "user-1") {
  return enqueueCheckIn(PAYLOAD, {
    clientId,
    recordedAt: new Date(NOW - minutesAgo * 60_000).toISOString(),
    userId,
  });
}

interface Sent {
  path: string;
  body: Record<string, unknown>;
}

/** A client whose POST /mood answers with a scripted outcome per call. */
function client(
  outcomes: Array<"ok" | Error | ((body: Record<string, unknown>) => MoodCheckInResponse)>,
) {
  const sent: Sent[] = [];
  const fake = {
    post: async (path: string, body: Record<string, unknown>): Promise<MoodCheckInResponse> => {
      sent.push({ path, body });
      const outcome = outcomes.shift() ?? "ok";
      if (outcome instanceof Error) {
        throw outcome;
      }
      if (typeof outcome === "function") {
        return outcome(body);
      }
      return { entry: moodEntry({ note: String(body.note) }), streak: moodStreak(), crisis: null };
    },
  } as unknown as ApiClient;
  return { fake, sent };
}

afterEach(() => {
  __resetOutboxSyncForTests();
});

describe("the queue on the device", () => {
  it("stores a check-in with its key, its time and its account", () => {
    const item = queue("c-1");
    expect(item.status).toBe("pending");
    expect(loadOutbox()).toEqual([item]);
    expect(JSON.parse(window.localStorage.getItem(MOOD_OUTBOX_KEY) ?? "[]")).toHaveLength(1);
  });

  it("does not duplicate a re-queued key, and caps runaway growth (oldest go first)", () => {
    queue("same");
    queue("same");
    expect(loadOutbox()).toHaveLength(1);
    clearOutbox();
    for (let i = 0; i < OUTBOX_MAX_ITEMS + 5; i += 1) {
      queue(`c-${i}`);
    }
    const kept = loadOutbox();
    expect(kept).toHaveLength(OUTBOX_MAX_ITEMS);
    expect(kept[0]?.clientId).toBe("c-5");
  });

  it("drops malformed records instead of failing", () => {
    window.localStorage.setItem(MOOD_OUTBOX_KEY, JSON.stringify([{ nope: true }, "x", 3]));
    expect(loadOutbox()).toEqual([]);
    window.localStorage.setItem(MOOD_OUTBOX_KEY, "{not json");
    expect(loadOutbox()).toEqual([]);
  });

  it("notifies subscribers, and clearing removes the storage key", () => {
    let calls = 0;
    const unsubscribe = subscribeOutbox(() => {
      calls += 1;
    });
    queue("c-1");
    removeFromOutbox("c-1");
    unsubscribe();
    queue("c-2");
    expect(calls).toBe(2);
    clearOutbox();
    expect(window.localStorage.getItem(MOOD_OUTBOX_KEY)).toBeNull();
  });
});

describe("sending what is waiting", () => {
  it("sends oldest first, with client_id and recorded_at, and empties the queue", async () => {
    const first = queue("c-1", 30);
    queue("c-2", 10);
    const { fake, sent } = client(["ok", "ok"]);

    const result = await syncOutbox({ client: fake, userId: "user-1", now: NOW });

    expect(sent.map((call) => call.body.client_id)).toEqual(["c-1", "c-2"]);
    expect(sent[0]?.body.recorded_at).toBe(first.recordedAt);
    expect(sent[0]?.body.note).toBe("Rough night.");
    expect(sent[0]?.body.tz).toEqual(expect.any(String));
    expect(result.sent).toHaveLength(2);
    expect(result.interrupted).toBe(false);
    expect(loadOutbox()).toEqual([]);
  });

  it("stops at a network failure and keeps everything for later", async () => {
    queue("c-1");
    queue("c-2");
    const { fake, sent } = client([new TypeError("Failed to fetch")]);

    const result = await syncOutbox({ client: fake, userId: "user-1", now: NOW });

    expect(sent).toHaveLength(1);
    expect(result).toMatchObject({ interrupted: true, waiting: 2 });
    expect(loadOutbox().map((item) => [item.clientId, item.status, item.attempts])).toEqual([
      ["c-1", "pending", 1],
      ["c-2", "pending", 0],
    ]);
  });

  it("treats an offline session check as a network failure, not a rejection", async () => {
    queue("c-1");
    const { fake } = client([new NetworkUnavailableError()]);
    const result = await syncOutbox({ client: fake, userId: "user-1", now: NOW });
    expect(result.interrupted).toBe(true);
    expect(loadOutbox()[0]?.status).toBe("pending");
  });

  it("retries later when the safety engine is unavailable — a note is never sent unscreened", async () => {
    queue("c-1");
    const { fake } = client([new ApiError(503, "safety_unavailable", "Try again.", "r")]);
    const result = await syncOutbox({ client: fake, userId: "user-1", now: NOW });
    expect(result.interrupted).toBe(true);
    expect(loadOutbox()[0]?.status).toBe("pending");
  });

  it("marks a refused time as failed, keeps it visible, and carries on with the rest", async () => {
    queue("c-1");
    queue("c-2");
    const { fake } = client([
      new ApiError(422, "invalid_recorded_at", "In the future.", "r"),
      "ok",
    ]);

    const result = await syncOutbox({ client: fake, userId: "user-1", now: NOW });

    expect(result.sent.map((sent) => sent.item.clientId)).toEqual(["c-2"]);
    expect(result.failed.map((item) => [item.clientId, item.failure])).toEqual([
      ["c-1", "time_rejected"],
    ]);
    expect(loadOutbox().map((item) => [item.clientId, item.status])).toEqual([["c-1", "failed"]]);
  });

  it("never sends a check-in as a different account", async () => {
    queue("c-1", 5, "someone-else");
    const { fake, sent } = client([]);
    const result = await syncOutbox({ client: fake, userId: "user-1", now: NOW });
    expect(sent).toEqual([]);
    expect(result.failed[0]?.failure).toBe("account_changed");
  });

  it("does not send what the server would refuse as too old", async () => {
    enqueueCheckIn(PAYLOAD, {
      clientId: "old",
      recordedAt: new Date(NOW - OUTBOX_MAX_AGE_MS - 60_000).toISOString(),
      userId: "user-1",
    });
    const { fake, sent } = client([]);
    const result = await syncOutbox({ client: fake, userId: "user-1", now: NOW });
    expect(sent).toEqual([]);
    expect(result.failed[0]?.failure).toBe("too_old");
  });

  it("maps consent and session failures to their own reasons", async () => {
    queue("c-1");
    queue("c-2");
    const { fake } = client([
      new ApiError(403, "consent_required", "Agree first.", "r"),
      new ApiError(401, "token_expired", "Expired.", "r"),
    ]);
    const result = await syncOutbox({ client: fake, userId: "user-1", now: NOW });
    expect(result.failed.map((item) => item.failure)).toEqual([
      "consent_required",
      "session_expired",
    ]);
  });

  it("returns the crisis help a synced note needed", async () => {
    queue("c-1");
    const crisis = {
      level: "high" as const,
      stored_level: "crisis",
      source: "rules",
      matched_categories: [],
      rationale_codes: [],
      about_someone_else: false,
      locale: "en",
      region: "IN",
      message: null,
      emergency: null,
      resources: [],
      check_in_saved: true,
    };
    const { fake } = client([() => ({ entry: moodEntry(), streak: moodStreak(), crisis })]);
    const result = await syncOutbox({ client: fake, userId: "user-1", now: NOW });
    expect(result.sent[0]?.response.crisis).toEqual(crisis);
  });

  it("runs one sync at a time per tab", async () => {
    queue("c-1");
    const { fake, sent } = client(["ok"]);
    const [a, b] = await Promise.all([
      syncOutbox({ client: fake, userId: "user-1", now: NOW }),
      syncOutbox({ client: fake, userId: "user-1", now: NOW }),
    ]);
    expect(a).toBe(b);
    expect(sent).toHaveLength(1);
  });
});
