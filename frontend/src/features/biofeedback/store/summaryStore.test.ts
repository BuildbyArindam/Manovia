/**
 * Encrypted summary store tests (Day 42): round trip, ciphertext-at-rest,
 * tampering, deletion. Uses Node's real WebCrypto and fake-indexeddb (set up
 * in vitest.setup.ts) — real AES-GCM, real key material.
 */

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  DecryptionFailed,
  RECORD_FORMAT,
  SUMMARY_DB_NAME,
  SUMMARY_STORE,
  openSummaryStore,
  type StoredSummaryRecord,
  type SummaryStore,
} from "./summaryStore";
import { summarizeSession, trendPoints, type SessionSummary } from "../session/summary";
import type { WindowMetrics } from "../signal/types";

const WINDOW: WindowMetrics = {
  startMs: 0,
  endMs: 60_000,
  acceptedBeats: 70,
  rejectedBeats: 0,
  gapMs: 0,
  quality: "good",
  qualityScore: 1,
  rmssdMs: 42.5,
  meanHrBpm: 71.2,
};

function summary(note: string | null = "the dog slept on my feet") {
  return summarizeSession({
    startedAtMs: Date.parse("2026-10-16T18:00:00Z"),
    endedAtMs: Date.parse("2026-10-16T18:10:00Z"),
    windows: [WINDOW],
    preMood: 3,
    postMood: 4,
    paceRateStartBpm: 6,
    paceRateEndBpm: 5.75,
    autoAdjust: true,
    stopReason: "completed",
    note,
  });
}

/**
 * Every connection opened by a test is tracked and closed after it: an
 * open connection blocks `deleteDatabase`, and without that each test would
 * run on the previous test's data.
 */
const opened: SummaryStore[] = [];

async function open(): Promise<SummaryStore> {
  const store = await openSummaryStore();
  opened.push(store);
  return store;
}

function deleteDatabase(): Promise<void> {
  return new Promise((resolve) => {
    const req = indexedDB.deleteDatabase(SUMMARY_DB_NAME);
    req.onsuccess = () => resolve();
    req.onerror = () => resolve();
    req.onblocked = () => resolve();
  });
}

/** A one-shot read of the raw records, without going through the store. */
function withRawRecords<T>(
  action: (records: StoredSummaryRecord[], db: IDBDatabase) => Promise<T>,
): Promise<T> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(SUMMARY_DB_NAME);
    req.onsuccess = () => {
      const db = req.result;
      const getAll = db
        .transaction(SUMMARY_STORE, "readonly")
        .objectStore(SUMMARY_STORE)
        .getAll();
      getAll.onsuccess = () => {
        void action(getAll.result as StoredSummaryRecord[], db)
          .then((value) => {
            db.close();
            resolve(value);
          })
          .catch((error: unknown) => {
            db.close();
            reject(error);
          });
      };
      getAll.onerror = () => {
        db.close();
        reject(getAll.error);
      };
    };
    req.onerror = () => reject(req.error);
  });
}

/** Mutate one raw record in place (tampering tests). */
function mutateRawRecord(mutate: (record: StoredSummaryRecord) => StoredSummaryRecord): Promise<void> {
  return withRawRecords(async (records, db) => {
    const record = records[0];
    if (record === undefined) {
      throw new Error("no record to mutate");
    }
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(SUMMARY_STORE, "readwrite");
      tx.objectStore(SUMMARY_STORE).put(mutate(record));
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
  });
}

beforeEach(deleteDatabase);

afterEach(async () => {
  for (const store of opened) {
    store.close();
  }
  opened.length = 0;
  await deleteDatabase();
});

describe("openSummaryStore", () => {
  it("saves and loads a summary (round trip)", async () => {
    const store = await open();
    const original = summary();
    const id = await store.save(original);
    const loaded = (await store.load(id)) as typeof original;
    expect(loaded).toEqual(original);
    expect(id).toBeTruthy();
  });

  it("keeps ciphertext at rest: no plaintext fields in the record", async () => {
    const store = await open();
    const id = await store.save(summary());
    await withRawRecords(async (records) => {
      expect(records.length).toBe(1);
      const record = records[0];
      expect(record?.format).toBe(RECORD_FORMAT);
      expect(record?.id).toBe(id);
      expect(record?.iv.length).toBe(12);
      expect(record?.ciphertext.length).toBeGreaterThan(0);
      const dump = JSON.stringify(records);
      // None of the sensitive values appear anywhere in the stored bytes.
      expect(dump).not.toContain("the dog slept on my feet");
      expect(dump).not.toContain("rmssdMs");
      expect(dump).not.toContain("42.5");
      expect(dump).not.toContain("2026-10-16T18:00:00.000Z");
    });
  });

  it("orders by save time (the trend view re-sorts by session date)", async () => {
    const store = await open();
    await store.save({ ...summary(), startedAt: "2026-10-16T18:00:00.000Z" });
    // Save times need to differ measurably (createdAt ties fall back to the
    // random id); 5 ms is plenty and keeps the test deterministic.
    await new Promise((resolve) => setTimeout(resolve, 5));
    await store.save({ ...summary(), startedAt: "2026-10-15T18:00:00.000Z" });
    const all = await store.loadAll();
    expect(all.length).toBe(2);
    const dates = all.map((entry) => (entry.summary as { startedAt: string }).startedAt);
    expect(dates).toEqual(["2026-10-16T18:00:00.000Z", "2026-10-15T18:00:00.000Z"]);
    // And trendPoints (summary.ts) orders by the session's own date.
    const ordered = trendPoints(all.map((e) => e.summary as SessionSummary));
    expect(ordered.map((p) => p.date)).toEqual([
      "2026-10-15T18:00:00.000Z",
      "2026-10-16T18:00:00.000Z",
    ]);
  });

  it("refuses a tampered record instead of showing wrong numbers", async () => {
    const store = await open();
    const id = await store.save(summary());
    await mutateRawRecord((record) => {
      const flipped = [...record.ciphertext];
      flipped[0] = ((flipped[0] ?? 0) + 1) % 256;
      return { ...record, ciphertext: flipped };
    });
    await expect(store.load(id)).rejects.toThrow(DecryptionFailed);
  });

  it("refuses a record bound to a different summary id (AAD)", async () => {
    const store = await open();
    await store.save(summary());
    await mutateRawRecord((record) => ({ ...record, id: "some-other-id" }));
    await expect(store.load("some-other-id")).rejects.toThrow(DecryptionFailed);
  });

  it("deletes one summary and all summaries", async () => {
    const store = await open();
    const a = await store.save(summary());
    const b = await store.save(summary(null));
    await store.delete(a);
    expect((await store.loadAll()).length).toBe(1);
    await store.deleteAll();
    expect(await store.loadAll()).toEqual([]);
    expect(await store.load(b)).toBeNull();
  });

  it("reuses one device key across opens (old summaries stay readable)", async () => {
    const first = await open();
    const id = await first.save(summary());
    const second = await open();
    expect(await second.load(id)).not.toBeNull();
  });
});
