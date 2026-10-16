/**
 * Local storage for Private Mode: settings, models, and the encrypted transcript.
 *
 * Three kinds of data, three different treatments:
 *
 *   settings   no secrets, so they are readable before the transcript is unlocked
 *              (the app has to know Private Mode is on, and which region to show
 *              helplines for, before anyone types a passphrase).
 *   models     bytes, verified by hash, deletable — the download manager owns them.
 *   messages   encrypted with a key derived from the user's passphrase, or not
 *              stored at all. There is no third state and no plaintext fallback: if
 *              no key exists, or storing is switched off, `append()` returns null and
 *              the conversation lives only in memory until the tab closes.
 *
 * The passphrase is never stored. What *is* stored is a verifier record — a known
 * string encrypted with the derived key — so unlocking can say "that is not the
 * passphrase" without keeping anything that helps guess it.
 */

import type { ModelSink, KeyValueStore } from "../models/downloadManager";
import type { DerivedKey, EncryptedRecord } from "../crypto/passphraseKey";
import {
  PBKDF2_ITERATIONS,
  RECORD_FORMAT,
  assessPassphrase,
  decryptString,
  deriveKey,
  encryptString,
  fromBase64,
  toBase64,
} from "../crypto/passphraseKey";
import type { MessagePayload, StoredMessageRecord } from "./idb";
import { messageKey } from "./idb";
import type { PrivateDatabase } from "./idb";

/** The conversation the unlock verifier lives in. */
export const UNLOCK_CONVERSATION = "__unlock__";
const VERIFIER_FIELD = "verifier";
const VERIFIER_PLAINTEXT = "manovia-private-unlock-v1";

/** Settings carry no secrets and no message text. */
export interface PrivateSettings {
  /** The toggle itself. */
  privateMode: boolean;
  /** Where helplines come from. Null means "use the fallback set". */
  region: string | null;
  locale: string | null;
  /** When false, nothing is written even if a key exists. */
  storeTranscript: boolean;
  /** Public key material, so unlocking can re-derive the same key. */
  keyId: string | null;
  keySalt: string | null;
  keyIterations: number | null;
  /** The encrypted known-string that proves a passphrase is the right one. */
  verifier: EncryptedRecord | null;
  /** What the user has agreed to. Timestamps, so the panel can show them. */
  consent: {
    capabilityCheckedAt: string | null;
    transcriptAt: string | null;
    modelsAt: string | null;
  };
}

export const DEFAULT_SETTINGS: PrivateSettings = {
  privateMode: false,
  region: null,
  locale: null,
  storeTranscript: true,
  keyId: null,
  keySalt: null,
  keyIterations: null,
  verifier: null,
  consent: { capabilityCheckedAt: null, transcriptAt: null, modelsAt: null },
};

const SETTINGS_KEY = "settings";

/** The model sink and key-value store, over the one Private Mode database. */
export class IndexedDbSink implements ModelSink {
  constructor(private readonly database: PrivateDatabase) {}

  async size(key: string): Promise<number> {
    const blob = await this.database.get<Uint8Array>("blobs", key);
    return blob?.byteLength ?? 0;
  }

  async read(key: string): Promise<Uint8Array | null> {
    return (await this.database.get<Uint8Array>("blobs", key)) ?? null;
  }

  async appendAt(key: string, offset: number, bytes: Uint8Array): Promise<number> {
    return this.database.appendAt(key, offset, bytes);
  }

  async delete(key: string): Promise<void> {
    await this.database.delete("blobs", key);
  }

  async keys(): Promise<string[]> {
    return this.database.keys("blobs");
  }

  async usageBytes(): Promise<number> {
    return this.database.usageBytes();
  }
}

export class IndexedDbKeyValue implements KeyValueStore {
  constructor(private readonly database: PrivateDatabase) {}

  async get<T>(key: string): Promise<T | undefined> {
    return this.database.get<T>("state", key);
  }

  async put<T>(key: string, value: T): Promise<void> {
    await this.database.put("state", key, value);
  }

  async delete(key: string): Promise<void> {
    await this.database.delete("state", key);
  }

  async keys(): Promise<string[]> {
    return this.database.keys("state");
  }
}

/** A partial update. `consent` is nested, so it gets its own partial. */
export interface SettingsPatch extends Omit<Partial<PrivateSettings>, "consent"> {
  consent?: Partial<PrivateSettings["consent"]>;
}

export class SettingsStore {
  constructor(private readonly database: PrivateDatabase) {}

  async load(): Promise<PrivateSettings> {
    const stored = await this.database.get<Partial<PrivateSettings>>("state", SETTINGS_KEY);
    if (stored === undefined) {
      return { ...DEFAULT_SETTINGS, consent: { ...DEFAULT_SETTINGS.consent } };
    }
    return {
      ...DEFAULT_SETTINGS,
      ...stored,
      consent: { ...DEFAULT_SETTINGS.consent, ...(stored.consent ?? {}) },
    };
  }

  async save(patch: SettingsPatch): Promise<PrivateSettings> {
    const current = await this.load();
    const next: PrivateSettings = {
      ...current,
      ...patch,
      consent: { ...current.consent, ...(patch.consent ?? {}) },
    };
    await this.database.put("state", SETTINGS_KEY, next);
    return next;
  }
}

export interface UnlockResult {
  ok: boolean;
  /** Set when the passphrase was wrong, so the UI can say that plainly. */
  reason?: "wrong-passphrase" | "no-key";
}

export interface StoredTurn {
  record: StoredMessageRecord;
  payload: MessagePayload;
}

/**
 * The transcript. Every read and write goes through the derived key; the class holds
 * the key in memory only, and `lock()` drops it.
 */
export class TranscriptStore {
  private readonly database: PrivateDatabase;
  private readonly settings: SettingsStore;
  private key: DerivedKey | null = null;

  constructor(database: PrivateDatabase, settings: SettingsStore) {
    this.database = database;
    this.settings = settings;
  }

  get locked(): boolean {
    return this.key === null;
  }

  get keyId(): string | null {
    return this.key?.keyId ?? null;
  }

  /** True when a passphrase has been set and the transcript can be unlocked. */
  async hasKey(): Promise<boolean> {
    const settings = await this.settings.load();
    return settings.verifier !== null && settings.keySalt !== null;
  }

  /**
   * Create the key for a passphrase, and store the verifier with it.
   *
   * Refuses a passphrase that `assessPassphrase` rejects: this key is the only thing
   * between a disk image and somebody's crisis conversation, and there is no
   * recovery path, so a weak one is a mistake worth blocking at the door.
   */
  async createKey(passphrase: string): Promise<{ ok: boolean; advice: string }> {
    const assessment = assessPassphrase(passphrase);
    if (!assessment.ok) {
      return { ok: false, advice: assessment.advice };
    }
    const derived = await deriveKey(passphrase);
    const verifier = await encryptString(derived, VERIFIER_PLAINTEXT, {
      conversationId: UNLOCK_CONVERSATION,
      field: VERIFIER_FIELD,
    });
    await this.settings.save({
      keyId: derived.keyId,
      keySalt: toBase64(derived.salt),
      keyIterations: derived.iterations,
      verifier,
      consent: { transcriptAt: derived.createdAt },
    });
    this.key = derived;
    return { ok: true, advice: "" };
  }

  /** Re-derive the key from a passphrase and check it against the verifier. */
  async unlock(passphrase: string): Promise<UnlockResult> {
    const settings = await this.settings.load();
    if (settings.verifier === null || settings.keySalt === null) {
      return { ok: false, reason: "no-key" };
    }
    const derived = await deriveKey(passphrase, {
      salt: fromBase64(settings.keySalt),
      iterations: settings.keyIterations ?? PBKDF2_ITERATIONS,
    });
    if (derived.keyId !== settings.keyId) {
      // Same salt and iterations must give the same key id; if not, the settings and
      // the derivation have drifted and nothing should be written.
      return { ok: false, reason: "wrong-passphrase" };
    }
    try {
      const plaintext = await decryptString(derived, settings.verifier);
      if (plaintext !== VERIFIER_PLAINTEXT) {
        return { ok: false, reason: "wrong-passphrase" };
      }
    } catch {
      return { ok: false, reason: "wrong-passphrase" };
    }
    this.key = derived;
    return { ok: true };
  }

  /** Drop the key. Nothing more can be read or written until `unlock()`. */
  lock(): void {
    this.key = null;
  }

  /**
   * Change the passphrase and re-encrypt everything under the new key.
   *
   * Records are not left behind under the old key id: a rotation that only applied to
   * new messages would leave the old transcript decryptable by a passphrase the user
   * believes they have retired.
   */
  async rotate(newPassphrase: string): Promise<{ ok: boolean; advice: string; moved: number }> {
    const assessment = assessPassphrase(newPassphrase);
    if (!assessment.ok) {
      return { ok: false, advice: assessment.advice, moved: 0 };
    }
    if (this.key === null) {
      return { ok: false, advice: "unlock the transcript first", moved: 0 };
    }
    const oldKey = this.key;
    const conversations = await this.conversationIds();
    const moved: StoredTurn[] = [];
    for (const conversationId of conversations) {
      for (const turn of await this.readWith(conversationId, oldKey)) {
        moved.push({ record: turn.record, payload: turn.payload });
      }
    }
    const derived = await deriveKey(newPassphrase);
    for (const turn of moved) {
      await this.database.delete("messages", turn.record.id);
    }
    this.key = derived;
    let written = 0;
    for (const conversationId of conversations) {
      const turns = moved.filter((turn) => turn.record.conversationId === conversationId);
      for (const [index, turn] of turns.entries()) {
        await this.write(conversationId, index, turn.payload, derived);
        written += 1;
      }
    }
    const verifier = await encryptString(derived, VERIFIER_PLAINTEXT, {
      conversationId: UNLOCK_CONVERSATION,
      field: VERIFIER_FIELD,
    });
    await this.settings.save({
      keyId: derived.keyId,
      keySalt: toBase64(derived.salt),
      keyIterations: derived.iterations,
      verifier,
    });
    return { ok: true, advice: "", moved: written };
  }

  /**
   * Store one turn. Returns null when nothing was written, which happens when the
   * transcript is switched off, when no key exists, or when the store is locked — the
   * caller shows the "not stored" badge rather than pretending it was saved.
   */
  async append(
    conversationId: string,
    payload: MessagePayload,
  ): Promise<StoredMessageRecord | null> {
    const settings = await this.settings.load();
    if (!settings.storeTranscript || this.key === null) {
      return null;
    }
    const seq = await this.nextSeq(conversationId);
    return this.write(conversationId, seq, payload, this.key);
  }

  /** The decrypted turns of one conversation, in order. Empty when locked. */
  async list(conversationId: string): Promise<StoredTurn[]> {
    if (this.key === null) {
      return [];
    }
    return this.readWith(conversationId, this.key);
  }

  /** Conversation ids that have at least one record. */
  async conversationIds(): Promise<string[]> {
    const records = await this.database.all<StoredMessageRecord>("messages");
    return [...new Set(records.map((record) => record.conversationId))].filter(
      (id) => id !== UNLOCK_CONVERSATION,
    );
  }

  /** How many records exist, without decrypting anything. */
  async count(): Promise<number> {
    return this.database.count("messages");
  }

  /** Delete one conversation, or everything when no id is given. */
  async clear(conversationId?: string): Promise<number> {
    const records = await this.database.all<StoredMessageRecord>("messages");
    const targets =
      conversationId === undefined
        ? records
        : records.filter((record) => record.conversationId === conversationId);
    for (const record of targets) {
      await this.database.delete("messages", record.id);
    }
    return targets.length;
  }

  /**
   * Destroy the transcript *and* the key material: crypto-erasure.
   *
   * After this, records that somehow survived are unreadable, because the salt and
   * verifier that identified the key are gone. This is what the "delete everything"
   * button in the privacy panel calls.
   */
  async wipe(): Promise<{ messages: number }> {
    const messages = await this.clear();
    this.key = null;
    await this.settings.save({
      keyId: null,
      keySalt: null,
      keyIterations: null,
      verifier: null,
      storeTranscript: true,
      consent: { transcriptAt: null },
    });
    return { messages };
  }

  // ---------------------------------------------------------------- internals

  private async write(
    conversationId: string,
    seq: number,
    payload: MessagePayload,
    key: DerivedKey,
  ): Promise<StoredMessageRecord> {
    const field = `message:${seq}`;
    const record = await encryptString(key, JSON.stringify(payload), {
      conversationId,
      field,
    });
    const stored: StoredMessageRecord = {
      id: messageKey(conversationId, seq),
      conversationId,
      seq,
      format: RECORD_FORMAT,
      ciphertext: record.ciphertext,
      iv: record.iv,
      keyId: record.keyId,
    };
    await this.database.put("messages", stored.id, stored);
    return stored;
  }

  private async readWith(conversationId: string, key: DerivedKey): Promise<StoredTurn[]> {
    const records = await this.database.all<StoredMessageRecord>("messages");
    const turns: StoredTurn[] = [];
    for (const record of records
      .filter((entry) => entry.conversationId === conversationId)
      .sort((left, right) => left.seq - right.seq)) {
      try {
        const plaintext = await decryptString(key, {
          format: record.format as typeof RECORD_FORMAT,
          iv: record.iv,
          ciphertext: record.ciphertext,
          keyId: record.keyId,
          conversationId: record.conversationId,
          field: `message:${record.seq}`,
        });
        turns.push({ record, payload: JSON.parse(plaintext) as MessagePayload });
      } catch {
        // One unreadable record is not a reason to hide the rest, but it must not be
        // shown as an empty message either: the caller gets a gap it can label.
        continue;
      }
    }
    return turns;
  }

  private async nextSeq(conversationId: string): Promise<number> {
    const records = await this.database.all<StoredMessageRecord>("messages");
    return records
      .filter((record) => record.conversationId === conversationId)
      .reduce((max, record) => Math.max(max, record.seq + 1), 0);
  }
}
