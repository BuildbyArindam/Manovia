/**
 * The model download manager: consent, size, progress, resume, verify, delete.
 *
 * Five promises, each of which is a piece of this class:
 *
 * 1. **Explicit consent.** `download()` refuses without a recorded `consent()`.
 *    Consent is per model, timestamped, and stored with the size the user was shown,
 *    so "you agreed to 1.4 GB" is a fact on the device rather than a memory.
 * 2. **Size and progress.** Every byte is accounted for: received, expected, rate,
 *    and which file is in flight. Nothing here shows a spinner where a number would
 *    do.
 * 3. **Resumable.** Files are appended at a byte offset in IndexedDB and a retry
 *    sends `Range: bytes=<offset>-`. A closed tab, a flaky network or an explicit
 *    pause costs the in-flight chunk, not the download.
 * 4. **Integrity.** After the bytes land, each file is hashed and compared with the
 *    manifest's pinned sha256. A mismatch deletes the file and fails the install —
 *    a partial or tampered artifact is never loaded. Unpinned entries are refused
 *    outright (`installBlocker`), which is why `npm run models:pin` exists.
 * 5. **Deletable.** `deleteModel()` and `deleteAll()` remove the bytes and the state
 *    and report what was freed. Local means local, including locally gone.
 *
 * Everything external is injected — sink, fetcher, hasher, consent store, runtime
 * installer, clock — so the whole flow, including an interrupted download, a
 * corrupted chunk and a full delete, is testable offline (AGENTS.md: every external
 * dependency behind an interface with a Fake).
 */

import type { ModelEntry, ModelManifest } from "./manifest";
import { installBlocker, modelById } from "./manifest";
import type { ModelId, ModelPhase, ProgressListener, ProgressUpdate } from "./types";

/** Where model bytes live. IndexedDB in the browser; a Map in tests. */
export interface ModelSink {
  size(key: string): Promise<number>;
  read(key: string): Promise<Uint8Array | null>;
  appendAt(key: string, offset: number, bytes: Uint8Array): Promise<number>;
  delete(key: string): Promise<void>;
  keys(): Promise<string[]>;
  usageBytes(): Promise<number>;
}

export interface FetchOptions {
  /** Bytes already stored; the fetcher should ask for `Range: bytes=<n>-`. */
  rangeStart?: number;
  signal?: AbortSignal;
  /** Called for every chunk, with the offset it belongs at. */
  onChunk: (chunk: Uint8Array, offset: number) => Promise<void>;
}

export interface FetchOutcome {
  status: number;
  bytesReceived: number;
  /** False when the server ignored the Range header and sent the whole file. */
  resumed: boolean;
  /** Total size of the resource, when the server said. */
  totalBytes: number | null;
}

export interface RemoteFetcher {
  get(url: string, options: FetchOptions): Promise<FetchOutcome>;
}

export interface Hasher {
  sha256(bytes: Uint8Array): Promise<string>;
}

/** Tiny key-value store for consent and model state. */
export interface KeyValueStore {
  get<T>(key: string): Promise<T | undefined>;
  put<T>(key: string, value: T): Promise<void>;
  delete(key: string): Promise<void>;
  keys(): Promise<string[]>;
}

/** For runtimes that fetch their own artifacts (WebLLM caches through the Cache API). */
export interface RuntimeInstaller {
  install(entry: ModelEntry, listener?: ProgressListener): Promise<void>;
}

export interface ConsentRecord {
  modelId: ModelId;
  grantedAt: string;
  /** Bytes the user was shown when they agreed. */
  declaredBytes: number;
  /** Manifest revision, so a changed artifact asks again. */
  upstream: string | null;
}

export type DownloadFailure =
  | "consent-required"
  | "not-installable"
  | "integrity-failed"
  | "aborted"
  | "network"
  | "storage"
  | "runtime";

export interface ModelState {
  modelId: ModelId;
  phase: ModelPhase;
  receivedBytes: number;
  totalBytes: number;
  bytesPerSecond: number | null;
  consent: ConsentRecord | null;
  installedAt: string | null;
  error: { code: DownloadFailure; detail: string } | null;
  files: { path: string; bytes: number; verified: boolean }[];
}

export interface DownloadManagerOptions {
  manifest: ModelManifest;
  sink: ModelSink;
  fetcher: RemoteFetcher;
  hasher: Hasher;
  store: KeyValueStore;
  installer?: RuntimeInstaller;
  /** Injectable clock; defaults to `Date.now`/`new Date().toISOString()`. */
  now?: () => number;
  isoNow?: () => string;
  /** Refuse a download when free space is below this. Null skips the check. */
  freeBytes?: () => Promise<number | null>;
  minFreeBytes?: number;
}

/** Files larger than this are not hashed in one buffer; the runtime owns them. */
export const MAX_HASHABLE_BYTES = 512 * 1024 * 1024;

const STATE_PREFIX = "model:";
const CONSENT_PREFIX = "consent:";

/** Rolling window for the rate estimate, in samples. */
const RATE_SAMPLES = 6;

/** How often the model-state record is persisted during a download. */
const STATE_SAVE_INTERVAL_MS = 500;

export class DownloadManager {
  private readonly manifest: ModelManifest;
  private readonly sink: ModelSink;
  private readonly fetcher: RemoteFetcher;
  private readonly hasher: Hasher;
  private readonly store: KeyValueStore;
  private readonly installer: RuntimeInstaller | null;
  private readonly now: () => number;
  private readonly isoNow: () => string;
  private readonly freeBytes: (() => Promise<number | null>) | null;
  private readonly minFreeBytes: number;
  private readonly controllers = new Map<ModelId, AbortController>();

  constructor(options: DownloadManagerOptions) {
    this.manifest = options.manifest;
    this.sink = options.sink;
    this.fetcher = options.fetcher;
    this.hasher = options.hasher;
    this.store = options.store;
    this.installer = options.installer ?? null;
    this.now = options.now ?? (() => Date.now());
    this.isoNow = options.isoNow ?? (() => new Date().toISOString());
    this.freeBytes = options.freeBytes ?? null;
    this.minFreeBytes = options.minFreeBytes ?? 0;
  }

  /** Every model in the manifest, with its stored state. */
  async states(): Promise<ModelState[]> {
    const states: ModelState[] = [];
    for (const entry of this.manifest.models) {
      states.push(await this.stateOf(entry.id));
    }
    return states;
  }

  async stateOf(modelId: ModelId): Promise<ModelState> {
    const entry = modelById(this.manifest, modelId);
    const stored = await this.store.get<ModelState>(`${STATE_PREFIX}${modelId}`);
    const consent = await this.store.get<ConsentRecord>(`${CONSENT_PREFIX}${modelId}`);
    if (stored !== undefined) {
      return { ...stored, consent: consent ?? null };
    }
    return {
      modelId,
      phase: "not-downloaded",
      receivedBytes: 0,
      totalBytes: entry?.total_bytes ?? 0,
      bytesPerSecond: null,
      consent: consent ?? null,
      installedAt: null,
      error: null,
      files: (entry?.files ?? []).map((file) => ({ path: file.path, bytes: 0, verified: false })),
    };
  }

  /** What the consent screen shows: the model, its honest note, and its size. */
  async consentPromptFor(modelId: ModelId): Promise<{
    entry: ModelEntry;
    declaredBytes: number;
    blocker: string | null;
  }> {
    const entry = modelById(this.manifest, modelId);
    if (entry === undefined) {
      throw new Error(`no model named ${modelId} in the manifest`);
    }
    return {
      entry,
      declaredBytes: entry.total_bytes,
      blocker: installBlocker(entry),
    };
  }

  /** Record explicit consent. Without it, `download()` refuses. */
  async consent(modelId: ModelId): Promise<ConsentRecord> {
    const entry = modelById(this.manifest, modelId);
    if (entry === undefined) {
      throw new Error(`no model named ${modelId} in the manifest`);
    }
    const record: ConsentRecord = {
      modelId,
      grantedAt: this.isoNow(),
      declaredBytes: entry.total_bytes,
      upstream: entry.upstream,
    };
    await this.store.put(`${CONSENT_PREFIX}${modelId}`, record);
    return record;
  }

  /** Withdraw consent. Also deletes the bytes, because consent was for holding them. */
  async revokeConsent(modelId: ModelId): Promise<{ freedBytes: number }> {
    await this.store.delete(`${CONSENT_PREFIX}${modelId}`);
    return this.deleteModel(modelId);
  }

  /**
   * Download, resume and verify one model.
   *
   * Calling this again after a pause or a failure resumes: the stored byte count is
   * the starting offset. Progress is reported through `listener` for the UI.
   */
  async download(modelId: ModelId, listener?: ProgressListener): Promise<ModelState> {
    const entry = modelById(this.manifest, modelId);
    if (entry === undefined) {
      throw new Error(`no model named ${modelId} in the manifest`);
    }
    // Registered before any await: a pause or cancel that arrives while the consent
    // and space checks are still in flight has to find something to abort, otherwise
    // the download runs on and the UI shows "paused" over a live transfer.
    const controller = new AbortController();
    this.controllers.set(modelId, controller);
    const consent = await this.store.get<ConsentRecord>(`${CONSENT_PREFIX}${modelId}`);
    if (consent === undefined) {
      return this.abandon(
        modelId,
        await this.fail(
          modelId,
          "consent-required",
          "the user has not agreed to this download yet",
        ),
      );
    }
    if (consent.upstream !== entry.upstream) {
      return this.abandon(
        modelId,
        await this.fail(
          modelId,
          "consent-required",
          "the artifact changed since this download was agreed; ask again",
        ),
      );
    }
    const blocker = installBlocker(entry);
    if (blocker !== null) {
      return this.abandon(modelId, await this.fail(modelId, "not-installable", blocker));
    }

    if (this.freeBytes !== null && this.minFreeBytes > 0) {
      const free = await this.freeBytes();
      if (free !== null && free < entry.total_bytes + this.minFreeBytes) {
        return this.abandon(
          modelId,
          await this.fail(
            modelId,
            "storage",
            `not enough free space: ${entry.total_bytes} bytes wanted, ${free} available`,
          ),
        );
      }
    }

    try {
      if (entry.managed_by_runtime) {
        return await this.installViaRuntime(entry, controller, listener);
      }
      return await this.downloadFiles(entry, controller, listener);
    } catch (error) {
      if (controller.signal.aborted) {
        return this.pause(modelId);
      }
      const code: DownloadFailure = isAbort(error) ? "aborted" : "network";
      return this.fail(modelId, code, describe(error));
    } finally {
      this.controllers.delete(modelId);
    }
  }

  /** Stop the in-flight download but keep the bytes, so it can resume. */
  async pause(modelId: ModelId): Promise<ModelState> {
    this.controllers.get(modelId)?.abort(new Error("paused"));
    const state = await this.stateOf(modelId);
    return this.save({ ...state, phase: "paused", bytesPerSecond: null, error: null });
  }

  /** Cancel and discard: the bytes go too. */
  async cancel(modelId: ModelId): Promise<ModelState> {
    this.controllers.get(modelId)?.abort(new Error("cancelled"));
    await this.deleteModel(modelId);
    return this.stateOf(modelId);
  }

  /** Re-verify files already on the device. Used after an upgrade or a suspicion. */
  async verify(
    modelId: ModelId,
  ): Promise<{ ok: boolean; files: { path: string; ok: boolean; detail: string }[] }> {
    const entry = modelById(this.manifest, modelId);
    if (entry === undefined) {
      throw new Error(`no model named ${modelId} in the manifest`);
    }
    const results: { path: string; ok: boolean; detail: string }[] = [];
    for (const file of entry.files) {
      const key = fileKey(modelId, file.path);
      const stored = await this.sink.read(key);
      if (stored === null) {
        results.push({ path: file.path, ok: false, detail: "not downloaded" });
        continue;
      }
      if (file.sha256 === null) {
        results.push({ path: file.path, ok: false, detail: "no pinned hash" });
        continue;
      }
      if (stored.byteLength !== file.bytes) {
        results.push({
          path: file.path,
          ok: false,
          detail: `size ${stored.byteLength} does not match the manifest's ${file.bytes}`,
        });
        continue;
      }
      const digest = await this.hasher.sha256(stored);
      results.push({
        path: file.path,
        ok: digest === file.sha256,
        detail: digest === file.sha256 ? "verified" : "sha256 does not match the manifest",
      });
    }
    const ok = results.every((result) => result.ok);
    const state = await this.stateOf(modelId);
    await this.save({
      ...state,
      phase: ok ? "ready" : "failed",
      files: state.files.map((record) => ({
        ...record,
        verified: results.find((result) => result.path === record.path)?.ok ?? false,
      })),
      error: ok
        ? null
        : { code: "integrity-failed", detail: results.find((r) => !r.ok)?.detail ?? "" },
    });
    return { ok, files: results };
  }

  /** Bytes for a verified file, for the runtime adapters. Null when not installed. */
  async readModelFile(modelId: ModelId, path: string): Promise<Uint8Array | null> {
    const state = await this.stateOf(modelId);
    if (state.phase !== "ready") {
      return null;
    }
    return this.sink.read(fileKey(modelId, path));
  }

  async deleteModel(modelId: ModelId): Promise<{ freedBytes: number }> {
    this.controllers.get(modelId)?.abort(new Error("deleted"));
    const entry = modelById(this.manifest, modelId);
    let freed = 0;
    for (const file of entry?.files ?? []) {
      const key = fileKey(modelId, file.path);
      freed += await this.sink.size(key);
      await this.sink.delete(key);
    }
    // A runtime-managed model (WebLLM) holds its own cache; ask the sink for
    // anything under its prefix so nothing is left behind.
    for (const key of await this.sink.keys()) {
      if (key.startsWith(`${modelId}/`)) {
        freed += await this.sink.size(key);
        await this.sink.delete(key);
      }
    }
    await this.store.delete(`${STATE_PREFIX}${modelId}`);
    return { freedBytes: freed };
  }

  /** The "delete downloaded models" button. Consent records are kept: they are the
   * user's own decision, not model data, and deleting them would hide what was agreed. */
  async deleteAll(): Promise<{ freedBytes: number; models: ModelId[] }> {
    let freed = 0;
    const models: ModelId[] = [];
    for (const entry of this.manifest.models) {
      const result = await this.deleteModel(entry.id);
      if (result.freedBytes > 0) {
        models.push(entry.id);
      }
      freed += result.freedBytes;
    }
    return { freedBytes: freed, models };
  }

  /** Bytes on the device for Private Mode models. */
  async usageBytes(): Promise<number> {
    return this.sink.usageBytes();
  }

  // ---------------------------------------------------------------- internals

  private async downloadFiles(
    entry: ModelEntry,
    controller: AbortController,
    listener?: ProgressListener,
  ): Promise<ModelState> {
    const modelId = entry.id;
    let state = await this.stateOf(modelId);
    const meter = new RateMeter();
    // Persisting on every chunk buys nothing: the bytes are already durable in the
    // sink, and the record only has to be close enough to resume from.
    let lastSavedAt = 0;
    const files = entry.files.map((file) => ({
      entry: file,
      key: fileKey(modelId, file.path),
      stored: 0,
      verified: false,
    }));
    for (const file of files) {
      file.stored = await this.sink.size(file.key);
    }
    let received = files.reduce((sum, file) => sum + file.stored, 0);
    const total = entry.total_bytes || received;

    state = await this.save({
      ...state,
      phase: "downloading",
      receivedBytes: received,
      totalBytes: total,
      error: null,
      files: files.map((file) => ({
        path: file.entry.path,
        bytes: file.stored,
        verified: false,
      })),
    });
    this.emit(listener, state, entry.files[0]?.path);

    for (const file of files) {
      if (controller.signal.aborted) {
        return this.pause(modelId);
      }
      const offset = file.stored;
      // A stored file that already matches the manifest is done; this is what makes
      // a retry after a failed *later* file cheap.
      if (file.entry.bytes > 0 && offset === file.entry.bytes) {
        const digest = await this.digestOf(file.key);
        if (digest === file.entry.sha256) {
          file.verified = true;
          continue;
        }
        // Right size, wrong bytes: start this file over rather than appending to it.
        await this.sink.delete(file.key);
        file.stored = 0;
        received -= offset;
      }

      const start = file.stored;
      let cursor = start;
      const outcome = await this.fetcher.get(file.entry.url, {
        rangeStart: start > 0 ? start : undefined,
        signal: controller.signal,
        onChunk: async (chunk, chunkOffset) => {
          if (controller.signal.aborted) {
            throw new AbortError("paused");
          }
          // The offset the fetcher reports is authoritative: a server that ignored
          // the Range header restarts at 0 and the stored prefix must be discarded.
          const at = chunkOffset;
          if (at < cursor) {
            await this.sink.delete(file.key);
            received -= cursor;
            cursor = 0;
          }
          await this.sink.appendAt(file.key, at, chunk);
          cursor = at + chunk.byteLength;
          received += chunk.byteLength;
          meter.sample(this.now(), chunk.byteLength);
          state = {
            ...state,
            receivedBytes: received,
            totalBytes: total || received,
            bytesPerSecond: meter.rate(),
            files: state.files.map((record) =>
              record.path === file.entry.path ? { ...record, bytes: cursor } : record,
            ),
          };
          const nowMs = this.now();
          if (nowMs - lastSavedAt >= STATE_SAVE_INTERVAL_MS) {
            lastSavedAt = nowMs;
            await this.save(state);
          }
          this.emit(listener, state, file.entry.path);
        },
      });

      // An abort can land while the fetcher is blocked (a stall, a slow server, a
      // paused tab). Checking here is what makes `pause()` mean "paused" rather than
      // "failed with an integrity error about a file that never arrived".
      if (controller.signal.aborted) {
        return this.pause(modelId);
      }
      if (outcome.status >= 400) {
        return this.fail(modelId, "network", `HTTP ${outcome.status} for ${file.entry.path}`);
      }
      file.stored = await this.sink.size(file.key);
      received = files.reduce((sum, other) => sum + other.stored, 0);
      state = await this.save({
        ...state,
        receivedBytes: received,
        files: state.files.map((record) =>
          record.path === file.entry.path ? { ...record, bytes: file.stored } : record,
        ),
      });
      if (!outcome.resumed && start > 0) {
        // The server sent the whole file; `onChunk` already restarted the blob.
        file.stored = await this.sink.size(file.key);
      }
    }

    return this.verifyAndFinish(entry, state, listener);
  }

  private async installViaRuntime(
    entry: ModelEntry,
    controller: AbortController,
    listener?: ProgressListener,
  ): Promise<ModelState> {
    if (this.installer === null) {
      return this.fail(
        entry.id,
        "runtime",
        `${entry.runtime} manages its own artifacts and no installer was wired up`,
      );
    }
    let state = await this.stateOf(entry.id);
    state = await this.save({ ...state, phase: "loading", error: null, bytesPerSecond: null });
    this.emit(listener, state);
    try {
      await this.installer.install(entry, (update) => {
        if (controller.signal.aborted) {
          return;
        }
        state = {
          ...state,
          phase: update.phase === "ready" ? "ready" : "loading",
          receivedBytes: update.receivedBytes,
          totalBytes: update.totalBytes,
          bytesPerSecond: update.bytesPerSecond,
        };
        void this.save(state);
        listener?.(update);
      });
    } catch (error) {
      if (controller.signal.aborted) {
        return this.pause(entry.id);
      }
      return this.fail(entry.id, "runtime", describe(error));
    }
    if (controller.signal.aborted) {
      return this.pause(entry.id);
    }
    return this.save({
      ...state,
      phase: "ready",
      installedAt: this.isoNow(),
      error: null,
    });
  }

  private async verifyAndFinish(
    entry: ModelEntry,
    state: ModelState,
    listener?: ProgressListener,
  ): Promise<ModelState> {
    const verifying = await this.save({ ...state, phase: "verifying", bytesPerSecond: null });
    this.emit(listener, verifying);

    const files = [...state.files];
    for (const file of entry.files) {
      const key = fileKey(entry.id, file.path);
      const stored = await this.sink.read(key);
      if (stored === null) {
        return this.fail(entry.id, "integrity-failed", `${file.path} is missing after download`);
      }
      if (stored.byteLength > MAX_HASHABLE_BYTES) {
        return this.fail(
          entry.id,
          "integrity-failed",
          `${file.path} is too large to verify in the browser; this artifact must be runtime-managed`,
        );
      }
      if (file.sha256 === null) {
        return this.fail(entry.id, "not-installable", `${file.path} has no pinned hash`);
      }
      if (file.bytes > 0 && stored.byteLength !== file.bytes) {
        await this.sink.delete(key);
        return this.fail(
          entry.id,
          "integrity-failed",
          `${file.path} is ${stored.byteLength} bytes, the manifest says ${file.bytes}`,
        );
      }
      const digest = await this.hasher.sha256(stored);
      if (digest !== file.sha256) {
        // Delete it: a file that failed verification must not survive to be loaded,
        // and the next attempt starts clean.
        await this.sink.delete(key);
        return this.fail(
          entry.id,
          "integrity-failed",
          `${file.path} failed its sha256 check and was deleted`,
        );
      }
      const index = files.findIndex((record) => record.path === file.path);
      const existing = index >= 0 ? files[index] : undefined;
      if (existing !== undefined) {
        files[index] = { ...existing, bytes: stored.byteLength, verified: true };
      }
    }

    const ready = await this.save({
      ...verifying,
      phase: "ready",
      files,
      installedAt: this.isoNow(),
      error: null,
    });
    this.emit(listener, ready);
    return ready;
  }

  private async digestOf(key: string): Promise<string | null> {
    const stored = await this.sink.read(key);
    return stored === null ? null : this.hasher.sha256(stored);
  }

  /** Drop the abort controller for a download that never started. */
  private abandon(modelId: ModelId, state: ModelState): ModelState {
    if (this.controllers.get(modelId) === undefined) {
      return state;
    }
    this.controllers.delete(modelId);
    return state;
  }

  private async fail(modelId: ModelId, code: DownloadFailure, detail: string): Promise<ModelState> {
    const state = await this.stateOf(modelId);
    return this.save({
      ...state,
      phase: "failed",
      bytesPerSecond: null,
      error: { code, detail },
    });
  }

  private async save(state: ModelState): Promise<ModelState> {
    // Consent lives in its own record. The stored copy keeps a null so the two
    // cannot disagree after a revoke; `stateOf` re-attaches the real one.
    await this.store.put(`${STATE_PREFIX}${state.modelId}`, { ...state, consent: null });
    return state;
  }

  private emit(listener: ProgressListener | undefined, state: ModelState, file?: string): void {
    if (listener === undefined) {
      return;
    }
    const update: ProgressUpdate = {
      modelId: state.modelId,
      phase: state.phase,
      receivedBytes: state.receivedBytes,
      totalBytes: state.totalBytes,
      bytesPerSecond: state.bytesPerSecond,
      ...(file === undefined ? {} : { file }),
      ...(state.error === null ? {} : { detail: state.error.detail }),
    };
    listener(update);
  }
}

/** Storage key for one model file: `<model>/<path>`, matching the manifest's paths. */
export function fileKey(modelId: ModelId, path: string): string {
  return `${modelId}/${path}`;
}

class AbortError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "AbortError";
  }
}

function isAbort(error: unknown): boolean {
  return error instanceof Error && (error.name === "AbortError" || error.message === "paused");
}

function describe(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  return String(error);
}

/**
 * A rate estimate over the last few samples.
 *
 * Averages are smoothed rather than instantaneous: a byte-rate that jumps between 0
 * and 40 MB/s on every chunk makes a progress bar unreadable, and an unreadable
 * progress bar is worse than none during a two-minute download.
 */
class RateMeter {
  private readonly samples: { at: number; bytes: number }[] = [];

  sample(at: number, bytes: number): void {
    this.samples.push({ at, bytes });
    if (this.samples.length > RATE_SAMPLES) {
      this.samples.shift();
    }
  }

  rate(): number | null {
    if (this.samples.length < 2) {
      return null;
    }
    const first = this.samples[0];
    const last = this.samples[this.samples.length - 1];
    if (first === undefined || last === undefined) {
      return null;
    }
    const seconds = (last.at - first.at) / 1000;
    if (seconds <= 0) {
      return null;
    }
    const bytes = this.samples.reduce((sum, sample) => sum + sample.bytes, 0) - first.bytes;
    return Math.max(0, Math.round(bytes / seconds));
  }
}

/** An in-memory sink, for tests. Behaves like IndexedDB including appendAt's rules. */
export class InMemorySink implements ModelSink {
  private readonly blobs = new Map<string, Uint8Array>();

  async size(key: string): Promise<number> {
    return this.blobs.get(key)?.byteLength ?? 0;
  }

  async read(key: string): Promise<Uint8Array | null> {
    return this.blobs.get(key) ?? null;
  }

  async appendAt(key: string, offset: number, bytes: Uint8Array): Promise<number> {
    const current = this.blobs.get(key) ?? new Uint8Array(0);
    if (offset > current.byteLength) {
      throw new Error(`cannot append at ${offset}: only ${current.byteLength} bytes stored`);
    }
    const grown = new Uint8Array(Math.max(current.byteLength, offset + bytes.byteLength));
    grown.set(current.subarray(0, offset), 0);
    grown.set(bytes, offset);
    this.blobs.set(key, grown);
    return grown.byteLength;
  }

  async delete(key: string): Promise<void> {
    this.blobs.delete(key);
  }

  async keys(): Promise<string[]> {
    return [...this.blobs.keys()];
  }

  async usageBytes(): Promise<number> {
    let total = 0;
    for (const blob of this.blobs.values()) {
      total += blob.byteLength;
    }
    return total;
  }

  /** Test hook: overwrite a stored file, e.g. to corrupt one byte. */
  corrupt(key: string, at: number, value: number): void {
    const blob = this.blobs.get(key);
    if (blob === undefined || at >= blob.byteLength) {
      throw new Error(`cannot corrupt ${key} at ${at}`);
    }
    blob[at] = value;
  }
}

/** An in-memory key-value store, for tests. */
export class InMemoryKeyValueStore implements KeyValueStore {
  private readonly records = new Map<string, unknown>();

  async get<T>(key: string): Promise<T | undefined> {
    return this.records.get(key) as T | undefined;
  }

  async put<T>(key: string, value: T): Promise<void> {
    this.records.set(key, value);
  }

  async delete(key: string): Promise<void> {
    this.records.delete(key);
  }

  async keys(): Promise<string[]> {
    return [...this.records.keys()];
  }
}
