/**
 * A small IndexedDB layer: one database, three object stores.
 *
 * Private Mode keeps everything on the device, and "everything" is three quite
 * different shapes:
 *
 *   state    small JSON records (consent, model phase, settings) — read on open
 *   blobs    model files, written incrementally so a download can be resumed
 *   messages the encrypted transcript (see crypto/encryptedStore.ts)
 *
 * The wrapper is deliberately thin and promise-shaped. It does not hide IndexedDB's
 * transaction model, it just removes the ceremony: `open()` once, then `get/put/
 * delete/all` per store, plus `appendAt` for resumable writes.
 *
 * Why IndexedDB and not the Cache API for model bytes: a partial download has to be
 * *appended to* at a byte offset, and the Cache API stores whole responses only.
 * The Cache API is still used where it fits — WebLLM caches its own artifacts
 * through it, and the safety bundle is fetched with normal HTTP caching.
 */

/** Blob chunks are written in this size; 4 MB balances memory against syscalls. */
export const CHUNK_BYTES = 4 * 1024 * 1024;

const DATABASE = "manovia-private";
const VERSION = 1;
const STATE_STORE = "state";
const BLOB_STORE = "blobs";
const MESSAGE_STORE = "messages";

export type StoreName = typeof STATE_STORE | typeof BLOB_STORE | typeof MESSAGE_STORE;

/** Raised when IndexedDB is missing or a transaction aborts. Callers degrade. */
export class StorageUnavailable extends Error {
  constructor(message: string, options?: { cause?: unknown }) {
    super(message, options);
    this.name = "StorageUnavailable";
  }
}

let shared: Promise<PrivateDatabase> | null = null;

/** Open (or reuse) the Private Mode database. Rejects with StorageUnavailable. */
export function openPrivateDatabase(name: string = DATABASE): Promise<PrivateDatabase> {
  if (shared === null) {
    shared = open(name).catch((error) => {
      shared = null;
      throw error;
    });
  }
  return shared;
}

/**
 * Close the shared connection and drop the handle.
 *
 * Closing matters, not just forgetting: an open connection blocks `deleteDatabase`,
 * so a test that only dropped the handle would leave the previous case's records
 * behind and quietly assert against them.
 */
export async function closePrivateDatabase(): Promise<void> {
  const handle = shared;
  shared = null;
  if (handle === null) {
    return;
  }
  try {
    (await handle).close();
  } catch {
    // A connection that was never opened has nothing to close.
  }
}

function open(name: string): Promise<PrivateDatabase> {
  return new Promise((resolve, reject) => {
    if (typeof indexedDB === "undefined") {
      reject(new StorageUnavailable("this browser has no IndexedDB"));
      return;
    }
    const request = indexedDB.open(name, VERSION);
    request.onupgradeneeded = () => {
      const database = request.result;
      for (const store of [STATE_STORE, BLOB_STORE, MESSAGE_STORE]) {
        if (!database.objectStoreNames.contains(store)) {
          database.createObjectStore(store);
        }
      }
    };
    request.onsuccess = () => resolve(new PrivateDatabase(request.result));
    request.onerror = () =>
      reject(new StorageUnavailable("IndexedDB refused to open", { cause: request.error }));
    request.onblocked = () =>
      reject(new StorageUnavailable("IndexedDB is blocked by another open tab"));
  });
}

/**
 * A record in `messages`.
 *
 * Everything that says anything about the conversation is inside the ciphertext:
 * the role, the text, the risk tier and the time it was written. What stays in the
 * clear is only what a store needs to function — an id, the conversation it belongs
 * to, a monotonic sequence number for ordering, and the key material that is public
 * anyway (IV, key id). A stored level or a timestamp in the clear would tell anyone
 * reading IndexedDB which messages were crisis messages and when, which is exactly
 * the metadata the encryption exists to hide.
 *
 * The AAD binds each record to `conversationId` *and* `seq`, so records cannot be
 * reordered or moved between conversations without failing authentication.
 */
export interface StoredMessageRecord {
  id: string;
  conversationId: string;
  /** Monotonic within a conversation: ordering without a wall clock in the clear. */
  seq: number;
  format: string;
  /** Base64 of the AES-GCM ciphertext, auth tag included. */
  ciphertext: string;
  /** Base64 of the per-record IV. Never reused with a key. */
  iv: string;
  /** Key id, so a rotated key does not silently orphan old records. */
  keyId: string;
}

/** What is inside a message ciphertext. Never written to IndexedDB in this shape. */
export interface MessagePayload {
  role: "user" | "assistant";
  text: string;
  /** The four-tier stored label (`none`, `caution`, `elevated`, `crisis`). */
  storedLevel: string | null;
  createdAt: string;
  /** True when the turn was answered with the deterministic crisis card. */
  crisis: boolean;
}

/** IndexedDB key for one message: `<conversationId>#<seq>`, so a range read is a prefix. */
export function messageKey(conversationId: string, seq: number): string {
  return `${conversationId}#${String(seq).padStart(8, "0")}`;
}

export class PrivateDatabase {
  private readonly database: IDBDatabase;

  constructor(database: IDBDatabase) {
    this.database = database;
  }

  close(): void {
    this.database.close();
  }

  async get<T>(store: StoreName, key: string): Promise<T | undefined> {
    return this.run(store, "readonly", (objectStore) => objectStore.get(key) as IDBRequest<T>);
  }

  async all<T>(store: StoreName): Promise<T[]> {
    return this.run(store, "readonly", (objectStore) => objectStore.getAll() as IDBRequest<T[]>);
  }

  async keys(store: StoreName): Promise<string[]> {
    return this.run(
      store,
      "readonly",
      (objectStore) => objectStore.getAllKeys() as IDBRequest<string[]>,
    );
  }

  async put<T>(store: StoreName, key: string, value: T): Promise<void> {
    await this.run(store, "readwrite", (objectStore) => objectStore.put(value, key));
  }

  async delete(store: StoreName, key: string): Promise<void> {
    await this.run(store, "readwrite", (objectStore) => objectStore.delete(key));
  }

  async clear(store: StoreName): Promise<void> {
    await this.run(store, "readwrite", (objectStore) => objectStore.clear());
  }

  async count(store: StoreName): Promise<number> {
    return this.run(store, "readonly", (objectStore) => objectStore.count());
  }

  /**
   * Write bytes at an offset, growing the stored blob.
   *
   * A resumed download needs exactly this: the first chunk lands at whatever
   * survived the interruption. IndexedDB values are immutable, so the blob is read,
   * extended and written back inside one transaction — acceptable at model-file
   * scale (tens of MB) and the reason `CHUNK_BYTES` is not tiny.
   */
  appendAt(key: string, offset: number, bytes: Uint8Array): Promise<number> {
    return new Promise<number>((resolve, reject) => {
      const transaction = this.database.transaction(BLOB_STORE, "readwrite");
      const objectStore = transaction.objectStore(BLOB_STORE);
      const read = objectStore.get(key) as IDBRequest<Uint8Array | undefined>;
      read.onerror = () =>
        reject(new StorageUnavailable("IndexedDB read failed", { cause: read.error }));
      read.onsuccess = () => {
        const current = read.result ?? new Uint8Array(0);
        if (offset > current.byteLength) {
          reject(
            new StorageUnavailable(
              `cannot append at ${offset}: only ${current.byteLength} bytes stored`,
            ),
          );
          return;
        }
        const grown = new Uint8Array(Math.max(current.byteLength, offset + bytes.byteLength));
        grown.set(current.subarray(0, offset), 0);
        grown.set(bytes, offset);
        const write = objectStore.put(grown, key);
        write.onerror = () =>
          reject(new StorageUnavailable("IndexedDB write failed", { cause: write.error }));
        write.onsuccess = () => resolve(grown.byteLength);
      };
      transaction.onabort = () =>
        reject(
          new StorageUnavailable("IndexedDB transaction aborted", { cause: transaction.error }),
        );
    });
  }

  /** Total bytes stored across all three stores, for the "delete models" panel. */
  async usageBytes(): Promise<number> {
    const blobs = await this.all<Uint8Array>(BLOB_STORE);
    const blobBytes = blobs.reduce((sum: number, blob) => sum + (blob?.byteLength ?? 0), 0);
    const [state, messages] = await Promise.all([
      this.all<unknown>(STATE_STORE),
      this.all<unknown>(MESSAGE_STORE),
    ]);
    const jsonBytes = [...state, ...messages].reduce(
      (sum: number, value) => sum + JSON.stringify(value ?? null).length,
      0,
    );
    return blobBytes + jsonBytes;
  }

  /** Run one request in its own transaction. */
  private run<T>(
    store: StoreName,
    mode: IDBTransactionMode,
    body: (objectStore: IDBObjectStore) => IDBRequest<T>,
  ): Promise<T> {
    return new Promise<T>((resolve, reject) => {
      if (!this.database.objectStoreNames.contains(store)) {
        reject(new StorageUnavailable(`no object store named ${store}`));
        return;
      }
      const transaction = this.database.transaction(store, mode);
      let request: IDBRequest<T>;
      try {
        request = body(transaction.objectStore(store));
      } catch (error) {
        reject(error instanceof Error ? error : new StorageUnavailable(String(error)));
        return;
      }
      request.onsuccess = () => resolve(request.result);
      request.onerror = () =>
        reject(new StorageUnavailable("IndexedDB request failed", { cause: request.error }));
      transaction.onabort = () =>
        reject(
          new StorageUnavailable("IndexedDB transaction aborted", { cause: transaction.error }),
        );
    });
  }
}
