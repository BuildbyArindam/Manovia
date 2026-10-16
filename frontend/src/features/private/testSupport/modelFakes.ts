/**
 * Test doubles for the model layer.
 *
 * The real runtimes cannot run here: no WebGPU, no network to Hugging Face, and no
 * ONNX artifacts (AGENTS.md forbids committing weights anyway). What *can* be tested
 * is everything around them — consent, progress accounting, resuming an interrupted
 * download, rejecting a corrupted file, deleting models, and a session that degrades
 * to template replies — so these doubles are written to behave like the real thing
 * at the boundaries that matter:
 *
 *   ScriptedFetcher   serves bytes over a fake network, in chunks, honouring (or
 *                     deliberately ignoring) `Range`, and can fail mid-transfer or
 *                     stall until aborted. That is what makes resume and integrity
 *                     testable rather than asserted.
 *   Sha256Hasher      the production hasher: real `crypto.subtle`, no stub.
 *   Fake*Model        the three runtime slots, with scripted outputs — including a
 *                     reply model that can be told to write something the output
 *                     guard must refuse.
 */

import type { ModelEntry, ModelManifest } from "../models/manifest";
import { parseModelManifest } from "../models/manifest";
import type {
  EmotionModel,
  EmotionResult,
  ModelId,
  ProgressListener,
  ProgressUpdate,
  ReplyModel,
  ReplyOutcome,
  ReplyRequest,
  SafetySignal,
  SafetySignalModel,
} from "../models/types";
import type {
  FetchOptions,
  FetchOutcome,
  Hasher,
  RemoteFetcher,
  RuntimeInstaller,
} from "../models/downloadManager";

/** Bytes for a fixture file. Deterministic, so a hash can be asserted. */
export function fixtureBytes(size: number, fill = 0x61): Uint8Array {
  const bytes = new Uint8Array(size);
  for (let index = 0; index < size; index += 1) {
    // Not a constant fill: a repeated byte would let an append-at-the-wrong-offset
    // bug hide, because the file would still look right.
    bytes[index] = (fill + index) % 251;
  }
  return bytes;
}

export function textBytes(text: string): Uint8Array {
  return new TextEncoder().encode(text);
}

export interface ScriptedResource {
  bytes: Uint8Array;
  /** How the fake network chunks it. Small chunks make progress assertions useful. */
  chunkSize?: number;
  /** Fail with a network error after this many bytes have been delivered. */
  failAfterBytes?: number;
  /** Ignore the Range header and send the whole file, as some CDNs do. */
  ignoreRange?: boolean;
  /** HTTP status to return instead of 200/206. */
  status?: number;
  /** Stall (never resolve) until the request is aborted. */
  stall?: boolean;
  /** Deliver chunks only when this resolves; used to test pause mid-transfer. */
  gate?: Promise<void>;
}

export interface FetchRecord {
  url: string;
  rangeStart: number;
  bytesDelivered: number;
  aborted: boolean;
}

/** A fake origin. One entry per URL; behaviour per entry is scripted. */
export class ScriptedFetcher implements RemoteFetcher {
  readonly requests: FetchRecord[] = [];
  private readonly resources: Map<string, ScriptedResource>;

  constructor(resources: Record<string, ScriptedResource | Uint8Array> = {}) {
    this.resources = new Map(
      Object.entries(resources).map(([url, value]) => [
        url,
        value instanceof Uint8Array ? { bytes: value } : value,
      ]),
    );
  }

  set(url: string, resource: ScriptedResource | Uint8Array): void {
    this.resources.set(url, resource instanceof Uint8Array ? { bytes: resource } : resource);
  }

  /** How many requests were made for a URL — the resume test's main assertion. */
  requestCount(url: string): number {
    return this.requests.filter((request) => request.url === url).length;
  }

  async get(url: string, options: FetchOptions): Promise<FetchOutcome> {
    const resource = this.resources.get(url);
    if (resource === undefined) {
      this.requests.push({
        url,
        rangeStart: options.rangeStart ?? 0,
        bytesDelivered: 0,
        aborted: false,
      });
      return { status: 404, bytesReceived: 0, resumed: false, totalBytes: 0 };
    }
    const record: FetchRecord = {
      url,
      rangeStart: options.rangeStart ?? 0,
      bytesDelivered: 0,
      aborted: false,
    };
    this.requests.push(record);

    const ignoreRange = resource.ignoreRange ?? false;
    const start = ignoreRange ? 0 : (options.rangeStart ?? 0);
    if (start > resource.bytes.byteLength) {
      return {
        status: 416,
        bytesReceived: 0,
        resumed: false,
        totalBytes: resource.bytes.byteLength,
      };
    }
    if (typeof resource.status === "number" && resource.status >= 400) {
      return {
        status: resource.status,
        bytesReceived: 0,
        resumed: false,
        totalBytes: resource.bytes.byteLength,
      };
    }
    if (resource.stall ?? false) {
      // Never resolves on its own: only an abort ends it, which is exactly what a
      // hung connection looks like from the manager's side.
      await new Promise<void>((resolve, reject) => {
        const signal = options.signal;
        if (signal?.aborted) {
          record.aborted = true;
          resolve();
          return;
        }
        signal?.addEventListener(
          "abort",
          () => {
            record.aborted = true;
            resolve();
          },
          { once: true },
        );
        void reject;
      });
      return {
        status: 0,
        bytesReceived: 0,
        resumed: start > 0,
        totalBytes: resource.bytes.byteLength,
      };
    }
    if (resource.gate !== undefined) {
      await resource.gate;
    }

    const chunkSize = resource.chunkSize ?? 1024;
    const failAfter = resource.failAfterBytes;
    let offset = start;
    let delivered = 0;
    while (offset < resource.bytes.byteLength) {
      if (options.signal?.aborted) {
        record.aborted = true;
        break;
      }
      const end = Math.min(offset + chunkSize, resource.bytes.byteLength);
      const chunk = resource.bytes.subarray(offset, end);
      if (failAfter !== undefined && delivered + chunk.byteLength > failAfter) {
        record.bytesDelivered = delivered;
        throw new Error("network dropped mid-transfer");
      }
      await options.onChunk(chunk, offset);
      delivered += chunk.byteLength;
      offset = end;
      // Let an abort that arrives during delivery be noticed before the next chunk.
      await Promise.resolve();
    }
    record.bytesDelivered = delivered;
    return {
      status: start > 0 && !ignoreRange ? 206 : 200,
      bytesReceived: delivered,
      resumed: start > 0 && !ignoreRange,
      totalBytes: resource.bytes.byteLength,
    };
  }
}

/** The production hasher. Uses real WebCrypto, which the test setup polyfills. */
export class Sha256Hasher implements Hasher {
  async sha256(bytes: Uint8Array): Promise<string> {
    const digest = await crypto.subtle.digest("SHA-256", bytes as BufferSource);
    return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
  }
}

/** Counts installs and reports progress like WebLLM does (phases, not bytes). */
export class FakeRuntimeInstaller implements RuntimeInstaller {
  readonly installed: ModelId[] = [];
  updates: ProgressUpdate[] = [];
  failWith: Error | null = null;

  async install(entry: ModelEntry, listener?: ProgressListener): Promise<void> {
    if (this.failWith !== null) {
      throw this.failWith;
    }
    const steps = [0.25, 0.5, 0.75, 1];
    for (const fraction of steps) {
      const update: ProgressUpdate = {
        modelId: entry.id,
        phase: fraction === 1 ? "ready" : "loading",
        receivedBytes: Math.round(1000 * fraction),
        totalBytes: 1000,
        bytesPerSecond: 500,
      };
      this.updates.push(update);
      listener?.(update);
    }
    this.installed.push(entry.id);
  }
}

/** A manifest whose files are real bytes with real hashes, so installs can succeed. */
export async function fixtureManifest(
  files: { modelId: ModelId; path: string; bytes: Uint8Array; url?: string }[],
  options: { unpinned?: ModelId[]; runtimeManaged?: ModelId[]; planned?: ModelId[] } = {},
): Promise<ModelManifest> {
  const hasher = new Sha256Hasher();
  const ids = [...new Set(files.map((file) => file.modelId))];
  for (const extra of [...(options.runtimeManaged ?? []), ...(options.planned ?? [])]) {
    if (!ids.includes(extra)) {
      ids.push(extra);
    }
  }
  const models = await Promise.all(
    ids.map(async (id): Promise<Record<string, unknown>> => {
      const owned = files.filter((file) => file.modelId === id);
      const managed = (options.runtimeManaged ?? []).includes(id);
      const planned = (options.planned ?? []).includes(id);
      const unpinned = (options.unpinned ?? []).includes(id);
      const entries = await Promise.all(
        owned.map(async (file) => ({
          path: file.path,
          kind: file.path.endsWith(".json") ? "config" : "weights",
          url: file.url ?? `https://models.invalid/${id}/${file.path}`,
          bytes: unpinned ? 0 : file.bytes.byteLength,
          sha256: unpinned ? null : await hasher.sha256(file.bytes),
        })),
      );
      return {
        id,
        kind: id === "reply" ? "causal-lm" : "sequence-classification",
        runtime: id === "reply" ? "webllm" : "transformers",
        capability: id === "reply" ? "replies" : "classifiers",
        status: planned ? "planned" : "available",
        label: `fixture ${id}`,
        honest_note: `a test double standing in for the real ${id} artifact`,
        upstream: `https://models.invalid/${id}`,
        licence: "MIT",
        managed_by_runtime: managed,
        labels: id === "emotion" ? ["neutral", "sadness", "joy"] : [],
        files: managed || planned ? [] : entries,
        total_bytes: managed || planned ? 0 : entries.reduce((sum, file) => sum + file.bytes, 0),
      };
    }),
  );
  return parseModelManifest({
    format: "manovia-model-manifest/1",
    generated_by: "testSupport/modelFakes.ts",
    description: "a fixture manifest for the download manager's tests",
    checksum_algorithm: "sha256",
    pinned: models.every((model) =>
      (model.files as { sha256: string | null }[]).every((file) => file.sha256 !== null),
    ),
    models,
  });
}

/** Keyword classifier double: deterministic, and honest that it is a double. */
export class FakeEmotionModel implements EmotionModel {
  readonly id = "emotion" as const;
  readonly capability = "classifiers" as const;
  readonly label = "Fake emotion classifier (test double)";
  readonly labels = ["neutral", "sadness", "joy", "anger", "fear"] as const;

  initCalls = 0;
  classified: string[] = [];
  disposed = false;
  failWith: Error | null = null;
  private loaded = false;

  get ready(): boolean {
    return this.loaded;
  }

  async init(listener?: ProgressListener): Promise<void> {
    this.initCalls += 1;
    listener?.({
      modelId: "emotion",
      phase: "loading",
      receivedBytes: 0,
      totalBytes: 1,
      bytesPerSecond: null,
    });
    if (this.failWith !== null) {
      throw this.failWith;
    }
    this.loaded = true;
    listener?.({
      modelId: "emotion",
      phase: "ready",
      receivedBytes: 1,
      totalBytes: 1,
      bytesPerSecond: null,
    });
  }

  async classify(text: string): Promise<EmotionResult> {
    if (!this.loaded) {
      await this.init();
    }
    this.classified.push(text);
    const lower = text.toLowerCase();
    const label =
      ["sad", "cry", "alone", "empty"].find((word) => lower.includes(word)) !== undefined
        ? "sadness"
        : ["angry", "furious", "rage"].find((word) => lower.includes(word)) !== undefined
          ? "anger"
          : ["afraid", "scared", "panic"].find((word) => lower.includes(word)) !== undefined
            ? "fear"
            : ["glad", "better", "hope"].find((word) => lower.includes(word)) !== undefined
              ? "joy"
              : "neutral";
    return {
      label,
      score: label === "neutral" ? 0.4 : 0.8,
      distribution: Object.fromEntries(this.labels.map((key) => [key, key === label ? 0.8 : 0.05])),
      source: "emotion",
    };
  }

  async dispose(): Promise<void> {
    this.loaded = false;
    this.disposed = true;
  }
}

/**
 * The safety signal double. It only ever raises caution: there is no code path here
 * that could lower a level, which is the property AGENTS.md requires of models.
 */
export class FakeSafetyModel implements SafetySignalModel {
  readonly id = "safety" as const;
  readonly capability = "classifiers" as const;
  readonly label = "Fake crisis-signal classifier (test double)";
  readonly threshold = 0.5;

  classified: string[] = [];
  private loaded = false;

  get ready(): boolean {
    return this.loaded;
  }

  async init(listener?: ProgressListener): Promise<void> {
    this.loaded = true;
    listener?.({
      modelId: "safety",
      phase: "ready",
      receivedBytes: 0,
      totalBytes: 0,
      bytesPerSecond: null,
    });
  }

  async classify(text: string): Promise<SafetySignal> {
    if (!this.loaded) {
      await this.init();
    }
    this.classified.push(text);
    const score = /kill myself|end it|no reason to live/i.test(text) ? 0.9 : 0.1;
    return {
      label: score >= this.threshold ? "crisis-adjacent" : "no-signal",
      score,
      raises_caution: score >= this.threshold,
      source: "safety",
    };
  }

  async dispose(): Promise<void> {
    this.loaded = false;
  }
}

/** A reply double that can be told to write something the guard must refuse. */
export class ScriptedReplyModel implements ReplyModel {
  readonly id = "reply" as const;
  readonly capability = "replies" as const;
  readonly label = "Scripted reply model (test double)";

  requests: ReplyRequest[] = [];
  chunks: string[] = [];
  /** What the next generate() returns, in order; the last entry repeats. */
  script: string[] = [];
  failWith: Error | null = null;
  tokensPerSecond: number | null = null;
  private loaded = false;

  constructor(script: string[] = ["That sounds heavy. What has today been like for you?"]) {
    this.script = [...script];
  }

  get ready(): boolean {
    return this.loaded;
  }

  async init(listener?: ProgressListener): Promise<void> {
    this.loaded = true;
    listener?.({
      modelId: "reply",
      phase: "ready",
      receivedBytes: 0,
      totalBytes: 0,
      bytesPerSecond: null,
    });
  }

  async generate(request: ReplyRequest, onChunk?: (text: string) => void): Promise<ReplyOutcome> {
    if (!this.loaded) {
      await this.init();
    }
    this.requests.push(request);
    if (this.failWith !== null) {
      throw this.failWith;
    }
    const index = Math.min(this.requests.length - 1, this.script.length - 1);
    const text = this.script[index] ?? "";
    for (const part of text.split(/(?<=\s)/).slice(0, 3)) {
      this.chunks.push(part);
      onChunk?.(part);
    }
    const tokens = text.split(/\s+/).filter(Boolean).length;
    this.tokensPerSecond = 42;
    return {
      text,
      tokens,
      tokensPerSecond: 42,
      runtimeLabel: "scripted test double",
      degraded: false,
      source: "reply",
    };
  }

  async dispose(): Promise<void> {
    this.loaded = false;
  }
}
