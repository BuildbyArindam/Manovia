/**
 * The local store, inspected the way an attacker with the disk would inspect it.
 *
 * The central assertion in this file is not that decryption works — it is that
 * IndexedDB, read raw, contains none of the conversation. Every test that writes a
 * message reads the record back *without* the key and looks for the plaintext, for
 * the risk tier, and for the timestamp. A transcript that is encrypted but leaks
 * "this message was a crisis at 23:40" has not kept the promise.
 *
 * Also covered: a wrong passphrase, a tampered ciphertext, a rotated key, records
 * moved between conversations, storing nothing at all, and deletion.
 */

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { StorageUnavailable, closePrivateDatabase, messageKey, openPrivateDatabase } from "./idb";
import type { PrivateDatabase, StoredMessageRecord } from "./idb";
import {
  DEFAULT_SETTINGS,
  IndexedDbKeyValue,
  IndexedDbSink,
  SettingsStore,
  TranscriptStore,
  UNLOCK_CONVERSATION,
} from "./localStore";
import type { MessagePayload } from "./idb";
import {
  DecryptionFailed,
  PBKDF2_ITERATIONS,
  assessPassphrase,
  decryptString,
  deriveKey,
  encryptString,
  fromBase64,
  toBase64,
} from "../crypto/passphraseKey";
import type { EncryptedRecord } from "../crypto/passphraseKey";

const PASSPHRASE = "the monsoon reached kolkata early this year";
const OTHER_PASSPHRASE = "a different sentence nobody would guess";
const CONVERSATION = "conv-private-1";

/** Message text that must never appear in a stored record. */
const SECRET_USER = "I have been thinking about ending my life tonight";
const SECRET_REPLY = "Thank you for telling me. Please call 112 now.";

let database: PrivateDatabase;
let settings: SettingsStore;
let transcript: TranscriptStore;

async function freshDatabase(): Promise<void> {
  await closePrivateDatabase();
  await new Promise<void>((resolve, reject) => {
    const request = indexedDB.deleteDatabase("manovia-private");
    request.onsuccess = () => resolve();
    request.onerror = () => reject(new Error("could not delete the test database"));
    request.onblocked = () => resolve();
  });
  database = await openPrivateDatabase();
  settings = new SettingsStore(database);
  transcript = new TranscriptStore(database, settings);
}

function payload(role: "user" | "assistant", text: string, level: string | null): MessagePayload {
  return {
    role,
    text,
    storedLevel: level,
    createdAt: "2026-10-16T22:15:00.000Z",
    crisis: level === "crisis",
  };
}

/** Read the messages store with no key at all, the way devtools would. */
async function rawRecords(): Promise<StoredMessageRecord[]> {
  return database.all<StoredMessageRecord>("messages");
}

/** Every string IndexedDB holds, in one blob, for substring assertions. */
async function rawDump(): Promise<string> {
  const [messages, state] = await Promise.all([
    database.all<unknown>("messages"),
    database.all<unknown>("state"),
  ]);
  return JSON.stringify([...messages, ...state]);
}

beforeEach(async () => {
  await freshDatabase();
});

afterEach(async () => {
  transcript.lock();
  await closePrivateDatabase();
});

describe("the passphrase", () => {
  it("asks for a sentence, and says why a weak one is refused", () => {
    expect(assessPassphrase(PASSPHRASE).ok).toBe(true);
    expect(assessPassphrase("password").reasons.join(", ")).toMatch(/too short/);
    expect(assessPassphrase("aaaaaaaaaaaaaaaa").reasons.join(", ")).toMatch(/repeated|three words/);
    expect(assessPassphrase("123456789012345").reasons.join(", ")).toMatch(/only digits/);
    expect(assessPassphrase("supercalifragilistic").reasons.join(", ")).toMatch(/single long word/);
    expect(assessPassphrase("two words").reasons.join(", ")).toMatch(/fewer than three words/);
    const weak = assessPassphrase("short");
    expect(weak.ok).toBe(false);
    expect(weak.advice).toMatch(/never stored/);
    expect(weak.advice).toMatch(/no way to recover/);
  });

  it("refuses to create a key from a passphrase it would have to apologise for", async () => {
    const result = await transcript.createKey("hunter2");
    expect(result.ok).toBe(false);
    expect(result.advice).toMatch(/three or more words/);
    expect(await transcript.hasKey()).toBe(false);
    expect(await rawRecords()).toEqual([]);
  });

  it("derives the same key from the same passphrase and salt, and a different one otherwise", async () => {
    const first = await deriveKey(PASSPHRASE);
    const again = await deriveKey(PASSPHRASE, { salt: first.salt, iterations: first.iterations });
    expect(again.keyId).toBe(first.keyId);
    // Same salt, different passphrase: the id must change, because it identifies the
    // key. An id derived from the salt alone would let a wrong passphrase look right.
    const other = await deriveKey(OTHER_PASSPHRASE, { salt: first.salt });
    expect(other.keyId).not.toBe(first.keyId);
    expect(other.keyId).toMatch(/^[0-9a-f]{16}$/);
    // And the same passphrase and salt is stable across derivations, which is what
    // makes unlocking possible at all.
    expect((await deriveKey(PASSPHRASE, { salt: first.salt })).keyId).toBe(first.keyId);
    expect(first.iterations).toBe(PBKDF2_ITERATIONS);
    expect(first.salt).toHaveLength(16);
  });
});

describe("encryption at rest", () => {
  beforeEach(async () => {
    const created = await transcript.createKey(PASSPHRASE);
    expect(created.ok).toBe(true);
    await transcript.append(CONVERSATION, payload("user", SECRET_USER, "crisis"));
    await transcript.append(CONVERSATION, payload("assistant", SECRET_REPLY, "crisis"));
  });

  it("stores ciphertext only: no text, no risk tier, no timestamp", async () => {
    const records = await rawRecords();
    expect(records).toHaveLength(2);
    for (const record of records) {
      expect(record.format).toBe("manovia-private-record/1");
      expect(record.ciphertext).toMatch(/^[A-Za-z0-9+/]+=*$/);
      expect(record.iv).toMatch(/^[A-Za-z0-9+/]+=*$/);
      expect(fromBase64(record.iv)).toHaveLength(12);
      expect(record.keyId).toMatch(/^[0-9a-f]{16}$/);
      expect(Object.keys(record).sort()).toEqual([
        "ciphertext",
        "conversationId",
        "format",
        "id",
        "iv",
        "keyId",
        "seq",
      ]);
    }
    const dump = await rawDump();
    for (const forbidden of [
      SECRET_USER,
      SECRET_REPLY,
      "ending my life",
      "crisis",
      "caution",
      "elevated",
      "2026-10-16T22:15",
      "assistant",
      '"role"',
      '"text"',
      "storedLevel",
    ]) {
      expect(dump, `IndexedDB leaked ${forbidden}`).not.toContain(forbidden);
    }
    // The conversation id is in the clear by design (it is the store key), and the
    // sequence number orders records without a wall clock.
    expect(records.map((record) => record.seq)).toEqual([0, 1]);
    expect(records[0]?.id).toBe(messageKey(CONVERSATION, 0));
  });

  it("uses a fresh IV per record, because reusing one with GCM is fatal", async () => {
    const records = await rawRecords();
    const ivs = records.map((record) => record.iv);
    expect(new Set(ivs).size).toBe(ivs.length);
    await transcript.append(CONVERSATION, payload("user", "and again", null));
    const after = await rawRecords();
    expect(new Set(after.map((record) => record.iv)).size).toBe(after.length);
  });

  it("reads the conversation back in order, decrypted", async () => {
    const turns = await transcript.list(CONVERSATION);
    expect(turns.map((turn) => turn.payload)).toEqual([
      payload("user", SECRET_USER, "crisis"),
      payload("assistant", SECRET_REPLY, "crisis"),
    ]);
  });

  it("says nothing is stored when the transcript is switched off", async () => {
    await settings.save({ storeTranscript: false });
    const stored = await transcript.append(CONVERSATION, payload("user", "another message", null));
    expect(stored).toBeNull();
    expect(await transcript.count()).toBe(2);
  });

  it("stores nothing at all when locked, rather than storing it in the clear", async () => {
    transcript.lock();
    expect(transcript.locked).toBe(true);
    const stored = await transcript.append(CONVERSATION, payload("user", "locked out", null));
    expect(stored).toBeNull();
    expect(await transcript.list(CONVERSATION)).toEqual([]);
    expect(await transcript.count()).toBe(2);
  });
});

describe("unlocking", () => {
  it("accepts the right passphrase and refuses the wrong one", async () => {
    await transcript.createKey(PASSPHRASE);
    await transcript.append(CONVERSATION, payload("user", SECRET_USER, "crisis"));
    transcript.lock();

    expect(await transcript.unlock("not the passphrase at all")).toEqual({
      ok: false,
      reason: "wrong-passphrase",
    });
    expect(transcript.locked).toBe(true);
    expect(await transcript.list(CONVERSATION)).toEqual([]);

    expect(await transcript.unlock(PASSPHRASE)).toEqual({ ok: true });
    expect(transcript.locked).toBe(false);
    expect((await transcript.list(CONVERSATION))[0]?.payload.text).toBe(SECRET_USER);
  });

  it("reports no-key before a passphrase has ever been set", async () => {
    expect(await transcript.hasKey()).toBe(false);
    expect(await transcript.unlock(PASSPHRASE)).toEqual({ ok: false, reason: "no-key" });
  });

  it("keeps only public key material in settings — never the passphrase", async () => {
    await transcript.createKey(PASSPHRASE);
    const stored = await settings.load();
    expect(stored.keyId).toMatch(/^[0-9a-f]{16}$/);
    expect(stored.keyIterations).toBe(PBKDF2_ITERATIONS);
    expect(stored.keySalt).not.toBeNull();
    expect(stored.verifier?.ciphertext).toBeTruthy();
    expect(JSON.stringify(stored)).not.toContain(PASSPHRASE);
    expect(JSON.stringify(stored)).not.toContain(monkeyPassphrase(PASSPHRASE));
  });
});

/** A cheap check that no obvious encoding of the passphrase landed in settings. */
function monkeyPassphrase(value: string): string {
  return toBase64(new TextEncoder().encode(value));
}

describe("tampering and binding", () => {
  it("skips a record whose ciphertext was edited, instead of showing garbage", async () => {
    await transcript.createKey(PASSPHRASE);
    await transcript.append(CONVERSATION, payload("user", SECRET_USER, "crisis"));
    await transcript.append(CONVERSATION, payload("assistant", SECRET_REPLY, "crisis"));

    const records = await rawRecords();
    const target = records[0];
    expect(target).toBeDefined();
    const bytes = fromBase64(target?.ciphertext ?? "");
    bytes[0] = (bytes[0] ?? 0) ^ 0xff;
    await database.put("messages", target?.id ?? "", {
      ...target,
      ciphertext: toBase64(bytes),
    } as StoredMessageRecord);

    const turns = await transcript.list(CONVERSATION);
    // The damaged record is a gap, not an empty message and not an exception.
    expect(turns).toHaveLength(1);
    expect(turns[0]?.payload.role).toBe("assistant");
  });

  it("refuses a record moved to another conversation, because the AAD binds it", async () => {
    await transcript.createKey(PASSPHRASE);
    await transcript.append(CONVERSATION, payload("user", SECRET_USER, "crisis"));
    const [record] = await rawRecords();
    expect(record).toBeDefined();
    const derived = await deriveKey(PASSPHRASE, {
      salt: fromBase64((await settings.load()).keySalt ?? ""),
    });
    const moved: EncryptedRecord = {
      format: record?.format as EncryptedRecord["format"],
      iv: record?.iv ?? "",
      ciphertext: record?.ciphertext ?? "",
      keyId: record?.keyId ?? "",
      conversationId: "some-other-conversation",
      field: `message:${record?.seq ?? 0}`,
    };
    await expect(decryptString(derived, moved)).rejects.toThrow(DecryptionFailed);
    await expect(decryptString(derived, moved)).rejects.toMatchObject({ reason: "bad-passphrase" });
    // And the same record read under its own conversation id decrypts fine.
    const ok = await decryptString(derived, { ...moved, conversationId: CONVERSATION });
    expect(JSON.parse(ok) as MessagePayload).toEqual(payload("user", SECRET_USER, "crisis"));
  });

  it("refuses a record written by a different key", async () => {
    const one = await deriveKey(PASSPHRASE);
    const two = await deriveKey(OTHER_PASSPHRASE);
    const record = await encryptString(one, "hello", {
      conversationId: CONVERSATION,
      field: "message:0",
    });
    await expect(decryptString(two, record)).rejects.toThrow(DecryptionFailed);
    await expect(decryptString(two, record)).rejects.toMatchObject({ reason: "wrong-key" });
    expect(await decryptString(one, record)).toBe("hello");
  });

  it("refuses a malformed envelope rather than guessing", async () => {
    const derived = await deriveKey(PASSPHRASE);
    const record = await encryptString(derived, "hello", {
      conversationId: CONVERSATION,
      field: "message:0",
    });
    await expect(
      decryptString(derived, { ...record, format: "something-else" as never }),
    ).rejects.toMatchObject({ reason: "malformed" });
    await expect(
      decryptString(derived, { ...record, iv: toBase64(new Uint8Array(4)) }),
    ).rejects.toMatchObject({ reason: "malformed" });
    await expect(decryptString(derived, { ...record, ciphertext: "!!!" })).rejects.toThrow(
      DecryptionFailed,
    );
  });
});

describe("rotating and destroying", () => {
  it("re-encrypts the whole transcript under a new passphrase", async () => {
    await transcript.createKey(PASSPHRASE);
    await transcript.append(CONVERSATION, payload("user", SECRET_USER, "crisis"));
    await transcript.append(CONVERSATION, payload("assistant", SECRET_REPLY, "crisis"));
    const before = await rawRecords();

    const rotated = await transcript.rotate(OTHER_PASSPHRASE);
    expect(rotated).toEqual({ ok: true, advice: "", moved: 2 });

    const after = await rawRecords();
    expect(after).toHaveLength(2);
    // New key id, new ciphertexts: nothing survives under the old key.
    expect(after.every((record) => record.keyId !== before[0]?.keyId)).toBe(true);
    expect(after.map((record) => record.ciphertext).sort()).not.toEqual(
      before.map((record) => record.ciphertext).sort(),
    );

    transcript.lock();
    expect(await transcript.unlock(PASSPHRASE)).toEqual({ ok: false, reason: "wrong-passphrase" });
    expect(await transcript.unlock(OTHER_PASSPHRASE)).toEqual({ ok: true });
    expect((await transcript.list(CONVERSATION)).map((turn) => turn.payload.text)).toEqual([
      SECRET_USER,
      SECRET_REPLY,
    ]);
  });

  it("will not rotate to a weak passphrase, or while locked", async () => {
    await transcript.createKey(PASSPHRASE);
    await transcript.append(CONVERSATION, payload("user", SECRET_USER, "crisis"));
    expect((await transcript.rotate("abc")).ok).toBe(false);
    expect(await transcript.count()).toBe(1);
    transcript.lock();
    expect((await transcript.rotate(OTHER_PASSPHRASE)).advice).toMatch(/unlock/);
  });

  it("deletes one conversation, then everything, then the key material", async () => {
    await transcript.createKey(PASSPHRASE);
    await transcript.append(CONVERSATION, payload("user", SECRET_USER, "crisis"));
    await transcript.append("conv-2", payload("user", "a different conversation", null));
    expect(await transcript.conversationIds().then((ids) => ids.sort())).toEqual([
      "conv-2",
      CONVERSATION,
    ]);

    expect(await transcript.clear(CONVERSATION)).toBe(1);
    expect(await transcript.count()).toBe(1);

    expect((await transcript.wipe()).messages).toBe(1);
    expect(await transcript.count()).toBe(0);
    expect(await transcript.hasKey()).toBe(false);
    expect(transcript.locked).toBe(true);
    const stored = await settings.load();
    expect(stored.keyId).toBeNull();
    expect(stored.keySalt).toBeNull();
    expect(stored.verifier).toBeNull();
    expect(await rawDump()).not.toContain(SECRET_USER);
  });

  it("never lists the unlock verifier as a conversation", async () => {
    await transcript.createKey(PASSPHRASE);
    expect(await transcript.conversationIds()).not.toContain(UNLOCK_CONVERSATION);
  });
});

describe("settings", () => {
  it("starts at the documented defaults and merges nested consent", async () => {
    expect(await settings.load()).toEqual(DEFAULT_SETTINGS);
    await settings.save({ privateMode: true, region: "IN" });
    await settings.save({ consent: { modelsAt: "2026-10-16T00:00:00.000Z" } });
    const stored = await settings.load();
    expect(stored.privateMode).toBe(true);
    expect(stored.region).toBe("IN");
    expect(stored.consent.modelsAt).toBe("2026-10-16T00:00:00.000Z");
    expect(stored.consent.transcriptAt).toBeNull();
    // Settings hold no message text and no secrets.
    expect(JSON.stringify(stored)).not.toContain("ciphertext");
  });
});

describe("the IndexedDB adapters", () => {
  it("round-trips model bytes through the sink, including appends at an offset", async () => {
    const sink = new IndexedDbSink(database);
    const bytes = new Uint8Array([1, 2, 3, 4, 5, 6, 7, 8]);
    expect(await sink.size("emotion/model.onnx")).toBe(0);
    await sink.appendAt("emotion/model.onnx", 0, bytes.subarray(0, 4));
    await sink.appendAt("emotion/model.onnx", 4, bytes.subarray(4));
    expect(await sink.size("emotion/model.onnx")).toBe(8);
    expect(Array.from((await sink.read("emotion/model.onnx")) ?? [])).toEqual(Array.from(bytes));
    expect(await sink.keys()).toEqual(["emotion/model.onnx"]);
    expect(await sink.usageBytes()).toBeGreaterThanOrEqual(8);
    await sink.delete("emotion/model.onnx");
    expect(await sink.read("emotion/model.onnx")).toBeNull();
    expect(await sink.keys()).toEqual([]);
  });

  it("refuses to append past the end of a stored blob", async () => {
    const sink = new IndexedDbSink(database);
    await expect(sink.appendAt("emotion/model.onnx", 10, new Uint8Array([1]))).rejects.toThrow(
      StorageUnavailable,
    );
  });

  it("stores model state under the key-value adapter", async () => {
    const store = new IndexedDbKeyValue(database);
    expect(await store.get("model:emotion")).toBeUndefined();
    await store.put("model:emotion", { phase: "ready" });
    expect(await store.get<{ phase: string }>("model:emotion")).toEqual({ phase: "ready" });
    expect(await store.keys()).toContain("model:emotion");
    await store.delete("model:emotion");
    expect(await store.get("model:emotion")).toBeUndefined();
  });
});
