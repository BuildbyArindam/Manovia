/**
 * Encrypted local storage for saved session summaries (Day 42).
 *
 * The rule: **data stays local unless the user explicitly saves a summary**,
 * and saved summaries are encrypted at rest. Raw RR intervals are never
 * persisted anywhere — a summary is metrics, moods, pace and an optional
 * note, nothing more.
 *
 * Construction (same house style as the journal envelope and Private Mode):
 *
 *   device key    AES-GCM-256, non-extractable, generated once via WebCrypto
 *                 and kept as a CryptoKey in IndexedDB (`keys` store)
 *   per record    fresh 12-byte random IV (never reused with a key)
 *   AAD           manovia:biofeedback:v1|summary=<id> — a record cannot be
 *   swapped between summaries without failing authentication
 *
 * WHAT THIS PROTECTS AGAINST
 *   - casual extraction: a copied storage dump or backup holds ciphertext;
 *     the browser keeps CryptoKey material outside the origin's data store on
 *     platforms that support it.
 *
 * WHAT THIS DOES NOT PROTECT AGAINST
 *   - someone with your unlocked browser profile (the key is right there);
 *   - a compromised device or a malicious extension with page access;
 *   - metadata: how many summaries exist and when they were written is
 *     visible in IndexedDB (that is not secret);
 *   - loss: clearing site data destroys the key and the summaries with it.
 *
 * A passphrase-wrapped key (like Day 41's Private Mode) is the honest upgrade
 * and is parked in PROGRESS.md — a wellness trend does not deserve a
 * passphrase prompt today.
 *
 * WELLNESS FEATURE. Not a medical device.
 */

export const SUMMARY_DB_NAME = "manovia-biofeedback";
export const SUMMARY_DB_VERSION = 1;
export const SUMMARY_STORE = "summaries";
export const KEY_STORE = "keys";
export const DEVICE_KEY_ID = "device";
export const RECORD_FORMAT = "manovia-biofeedback-summary-record/1";
const AAD_PREFIX = "manovia:biofeedback:v1";

export class DecryptionFailed extends Error {
  constructor(message: string) {
    super(message);
    this.name = "DecryptionFailed";
  }
}

export interface StoredSummaryRecord {
  format: typeof RECORD_FORMAT;
  id: string;
  createdAt: string;
  keyId: string;
  iv: number[];
  ciphertext: number[];
}

export interface SummaryStore {
  /** Encrypt and persist one summary. Returns its id. */
  save(summary: unknown): Promise<string>;
  /** Decrypt every saved summary (for the trend view). */
  loadAll(): Promise<Array<{ id: string; createdAt: string; summary: unknown }>>;
  /** Decrypt one summary. */
  load(id: string): Promise<unknown | null>;
  delete(id: string): Promise<void>;
  deleteAll(): Promise<void>;
  /** Close the IndexedDB connection (tests and teardown). */
  close(): void;
}

interface DeviceKey {
  key: CryptoKey;
  keyId: string;
}

function toBase64(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) {
    binary += String.fromCharCode(byte);
  }
  return btoa(binary);
}

/** number[] (structured-cloned) back into a BufferSource WebCrypto accepts. */
function toBufferSource(values: readonly number[]): BufferSource {
  const bytes = new Uint8Array(new ArrayBuffer(values.length));
  for (let i = 0; i < values.length; i += 1) {
    bytes[i] = values[i] ?? 0;
  }
  return bytes;
}

function request<T>(req: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error ?? new Error("IndexedDB request failed"));
  });
}

function openDatabase(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(SUMMARY_DB_NAME, SUMMARY_DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(SUMMARY_STORE)) {
        db.createObjectStore(SUMMARY_STORE, { keyPath: "id" });
      }
      if (!db.objectStoreNames.contains(KEY_STORE)) {
        db.createObjectStore(KEY_STORE);
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error ?? new Error("IndexedDB open failed"));
  });
}

async function getOrCreateDeviceKey(db: IDBDatabase): Promise<DeviceKey> {
  const store = db.transaction(KEY_STORE, "readwrite").objectStore(KEY_STORE);
  const existing = (await request(store.get(DEVICE_KEY_ID))) as
    | { key: CryptoKey; keyId: string }
    | undefined;
  const looksLikeCryptoKey =
    typeof existing?.key === "object" &&
    existing.key !== null &&
    typeof (existing.key as CryptoKey).algorithm === "object";
  if (existing !== undefined && looksLikeCryptoKey) {
    return existing;
  }
  const key = await crypto.subtle.generateKey({ name: "AES-GCM", length: 256 }, false, [
    "encrypt",
    "decrypt",
  ]);
  const random = crypto.getRandomValues(new Uint8Array(8));
  const keyId = toBase64(random);
  const record = { key, keyId };
  await request(db.transaction(KEY_STORE, "readwrite").objectStore(KEY_STORE).put(record, DEVICE_KEY_ID));
  return record;
}

function aad(id: string): BufferSource {
  return toBufferSource([...new TextEncoder().encode(`${AAD_PREFIX}|summary=${id}`)]);
}

/**
 * Open (and prepare) the encrypted summary store for this device. Creating the
 * device key happens once; a second open reuses it, so old summaries stay
 * readable.
 */
export async function openSummaryStore(): Promise<SummaryStore> {
  const db = await openDatabase();
  const deviceKey = await getOrCreateDeviceKey(db);

  async function save(summary: unknown): Promise<string> {
    const id =
      typeof crypto.randomUUID === "function"
        ? crypto.randomUUID()
        : toBase64(crypto.getRandomValues(new Uint8Array(16)));
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const plaintext = new TextEncoder().encode(JSON.stringify(summary));
    const ciphertext = await crypto.subtle.encrypt(
      { name: "AES-GCM", iv, additionalData: aad(id) },
      deviceKey.key,
      plaintext,
    );
    const record: StoredSummaryRecord = {
      format: RECORD_FORMAT,
      id,
      createdAt: new Date().toISOString(),
      keyId: deviceKey.keyId,
      iv: [...iv],
      ciphertext: [...new Uint8Array(ciphertext)],
    };
    await request(
      db.transaction(SUMMARY_STORE, "readwrite").objectStore(SUMMARY_STORE).put(record),
    );
    return id;
  }

  async function decryptRecord(record: StoredSummaryRecord): Promise<unknown> {
    if (record.format !== RECORD_FORMAT) {
      throw new DecryptionFailed("unknown record format");
    }
    if (record.keyId !== deviceKey.keyId) {
      throw new DecryptionFailed("record belongs to another device key");
    }
    try {
      const plaintext = await crypto.subtle.decrypt(
        {
          name: "AES-GCM",
          iv: toBufferSource(record.iv),
          additionalData: aad(record.id),
        },
        deviceKey.key,
        toBufferSource(record.ciphertext),
      );
      return JSON.parse(new TextDecoder().decode(plaintext)) as unknown;
    } catch {
      // Wrong key or tampering are indistinguishable by design.
      throw new DecryptionFailed("cannot decrypt this summary (tampered, or wrong key)");
    }
  }

  async function loadAll(): Promise<Array<{ id: string; createdAt: string; summary: unknown }>> {
    const records = (await request(
      db.transaction(SUMMARY_STORE, "readonly").objectStore(SUMMARY_STORE).getAll(),
    )) as StoredSummaryRecord[];
    const out: Array<{ id: string; createdAt: string; summary: unknown }> = [];
    for (const record of records) {
      out.push({
        id: record.id,
        createdAt: record.createdAt,
        summary: await decryptRecord(record),
      });
    }
    return out.sort((a, b) => a.createdAt.localeCompare(b.createdAt));
  }

  return {
    save,
    loadAll,
    async load(id: string): Promise<unknown | null> {
      const record = (await request(
        db
          .transaction(SUMMARY_STORE, "readonly")
          .objectStore(SUMMARY_STORE)
          .get(id),
      )) as StoredSummaryRecord | undefined;
      if (record === undefined) {
        return null;
      }
      return decryptRecord(record);
    },
    async delete(id: string): Promise<void> {
      await request(
        db.transaction(SUMMARY_STORE, "readwrite").objectStore(SUMMARY_STORE).delete(id),
      );
    },
    async deleteAll(): Promise<void> {
      await request(
        db.transaction(SUMMARY_STORE, "readwrite").objectStore(SUMMARY_STORE).clear(),
      );
    },
    close(): void {
      db.close();
    },
  };
}
