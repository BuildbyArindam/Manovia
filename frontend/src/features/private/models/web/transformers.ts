/**
 * The transformers.js / ONNX Runtime Web adapter: the distilled classifiers.
 *
 * Loaded through `moduleLoader` by specifier, at the moment the user consents to a
 * model — `@huggingface/transformers` is not a dependency of this repository (see the
 * loader's comment for why), so in this build `init()` raises `ModelUnavailable` and
 * the session degrades to the template writer. In a deployment that installs it, the
 * same code runs the real thing.
 *
 * Two rules from AGENTS.md shape it:
 *
 *   - a classifier may only ever *raise* caution. This adapter returns a label and a
 *     score and nothing else; the decision belongs to the rules engine and the
 *     session's `raiseOnCaution` policy.
 *   - never claim more than the artifact is. The label says "distilled" and "on
 *     device", and `honest_note` from the manifest is carried through to the UI.
 *
 * WHERE THE WEIGHTS COME FROM
 * If a `ModelSink` with verified files is supplied, the adapter points the runtime at
 * `/private/models/` and switches remote models off, so the bytes that were downloaded
 * through the consent-and-hash flow are the bytes that run. Without a sink the runtime
 * fetches from its own upstream, and the label says so — a download nobody hashed is
 * not presented as a verified one.
 */

import type { ModelEntry } from "../manifest";
import type {
  EmotionModel,
  EmotionResult,
  ModelId,
  ProgressListener,
  SafetySignal,
  SafetySignalModel,
} from "../types";
import { ModelUnavailable } from "../types";
import type { ModelSink } from "../downloadManager";
import type { ModuleLoader } from "./moduleLoader";
import { loadRuntimeModule, requireExport } from "./moduleLoader";

export const TRANSFORMERS_SPECIFIER = "@huggingface/transformers";
/** Where verified model files are served from when a sink is supplied. */
export const LOCAL_MODEL_PATH = "/private/models/";

interface ProgressItem {
  status?: string;
  file?: string;
  progress?: number;
  loaded?: number;
  total?: number;
}

type Pipeline = ((text: string, options?: { topk?: number }) => Promise<unknown>) & {
  dispose?: () => Promise<void>;
};

interface PipelineFactory {
  (
    task: string,
    model: string,
    options?: { progress_callback?: (item: ProgressItem) => void; device?: string },
  ): Promise<Pipeline>;
}

export interface TransformersOptions {
  modelId: "emotion" | "safety";
  /** The manifest entry: its upstream id, labels and honest note. */
  entry: ModelEntry;
  loader?: ModuleLoader;
  /** Verified local bytes; when present, remote fetching is switched off. */
  sink?: ModelSink | null;
  /** For the safety slot: the score at which it asks for more caution. */
  threshold?: number;
  /** WebGPU when the device has it, WASM otherwise. The runtime decides if null. */
  device?: "webgpu" | "wasm" | null;
}

/** What every classifier adapter produces, whichever slot it fills. */
export interface Classification {
  label: string;
  score: number;
  distribution: Record<string, number>;
}

export class TransformersClassifier {
  readonly id: ModelId;
  readonly capability = "classifiers" as const;
  readonly label: string;
  readonly entry: ModelEntry;

  private readonly loader: ModuleLoader | undefined;
  private readonly sink: ModelSink | null;
  private readonly device: "webgpu" | "wasm" | null;
  private pipeline: Pipeline | null = null;
  private loadedLocally = false;
  private initPromise: Promise<void> | null = null;

  constructor(options: TransformersOptions) {
    this.id = options.modelId;
    this.entry = options.entry;
    this.loader = options.loader;
    this.sink = options.sink ?? null;
    this.device = options.device ?? null;
    this.label = `${options.entry.label} — ${options.entry.runtime}, on device`;
  }

  get ready(): boolean {
    return this.pipeline !== null;
  }

  /** True when the weights came from the verified local cache. */
  get usesVerifiedLocalFiles(): boolean {
    return this.loadedLocally;
  }

  /**
   * Load the runtime and build the pipeline once.
   *
   * Concurrent callers share one promise: two `init()`s must not download a model
   * twice or build two pipelines, and the second caller must not see `ready` flip
   * half-way through.
   */
  init(listener?: ProgressListener): Promise<void> {
    if (this.initPromise === null) {
      this.initPromise = this.load(listener).catch((error) => {
        this.initPromise = null;
        throw error;
      });
    }
    return this.initPromise;
  }

  async classify(text: string): Promise<Classification> {
    if (this.pipeline === null) {
      await this.init();
    }
    const pipeline = this.pipeline;
    if (pipeline === null) {
      throw new ModelUnavailable(this.id, "the pipeline was not built");
    }
    const topk = Math.max(1, this.entry.labels.length || 5);
    let raw: unknown;
    try {
      raw = await pipeline(text, { topk });
    } catch (error) {
      throw new ModelUnavailable(
        this.id,
        `inference failed: ${error instanceof Error ? error.message : String(error)}`,
      );
    }
    return this.normalise(raw);
  }

  async dispose(): Promise<void> {
    const pipeline = this.pipeline;
    this.pipeline = null;
    this.initPromise = null;
    if (pipeline?.dispose !== undefined) {
      try {
        await pipeline.dispose();
      } catch {
        // A runtime that will not release its buffers is not worth failing over:
        // the page holds the reference, and the browser reclaims it on unload.
      }
    }
  }

  // ---------------------------------------------------------------- internals

  private async load(listener?: ProgressListener): Promise<void> {
    const module = await loadRuntimeModule({
      modelId: this.id,
      specifier: TRANSFORMERS_SPECIFIER,
      loader: this.loader,
    });
    const pipeline = requireExport<PipelineFactory>(
      module,
      ["pipeline"],
      this.id,
      TRANSFORMERS_SPECIFIER,
    );
    this.configureEnv(module, listener);

    const upstream = this.entry.upstream ?? "";
    const modelRef = upstream === "" ? this.entry.id : upstream;
    listener?.({
      modelId: this.id,
      phase: "loading",
      receivedBytes: 0,
      totalBytes: this.entry.total_bytes,
      bytesPerSecond: null,
      detail: `building the ${this.entry.kind} pipeline`,
    });
    try {
      this.pipeline = await pipeline("text-classification", modelRef, {
        progress_callback: (item) => this.onProgress(item, listener),
        ...(this.device === null ? {} : { device: this.device }),
      });
    } catch (error) {
      throw new ModelUnavailable(
        this.id,
        `could not build the pipeline: ${error instanceof Error ? error.message : String(error)}`,
      );
    }
    listener?.({
      modelId: this.id,
      phase: "ready",
      receivedBytes: this.entry.total_bytes,
      totalBytes: this.entry.total_bytes,
      bytesPerSecond: null,
      detail: this.loadedLocally ? "verified local files" : "fetched by the runtime",
    });
  }

  /**
   * Point the runtime at the verified files when we have them.
   *
   * Both keys are feature-detected: the runtime's environment object has moved between
   * versions, and silently writing to a key it no longer reads would leave the app
   * believing it was running local weights while it fetched remote ones.
   */
  private configureEnv(module: Record<string, unknown>, listener?: ProgressListener): void {
    const env = module.env as Record<string, unknown> | undefined;
    if (env === undefined || this.sink === null) {
      return;
    }
    if (!("allowRemoteModels" in env) || !("localModelPath" in env)) {
      listener?.({
        modelId: this.id,
        phase: "loading",
        receivedBytes: 0,
        totalBytes: this.entry.total_bytes,
        bytesPerSecond: null,
        detail: "this runtime version has no local-model switch; it will fetch upstream",
      });
      return;
    }
    env.allowRemoteModels = false;
    env.localModelPath = LOCAL_MODEL_PATH;
    this.loadedLocally = true;
  }

  private onProgress(item: ProgressItem, listener?: ProgressListener): void {
    if (listener === undefined) {
      return;
    }
    const status = item.status ?? "";
    const phase =
      status === "done" || status === "ready"
        ? "loading"
        : status === "download" || status === "progress" || status === "initiate"
          ? "downloading"
          : "loading";
    const total = typeof item.total === "number" ? item.total : this.entry.total_bytes;
    listener({
      modelId: this.id,
      phase,
      receivedBytes: typeof item.loaded === "number" ? item.loaded : 0,
      totalBytes: total,
      bytesPerSecond: null,
      ...(item.file === undefined ? {} : { file: item.file }),
      ...(typeof item.progress === "number"
        ? { detail: `${Math.round(item.progress)}% of ${item.file ?? "the model"}` }
        : {}),
    });
  }

  /**
   * The runtime's output shape varies by version: a single object, an array of
   * `{label, score}`, or `{labels, scores}`. All three are reduced to one label, one
   * score and a distribution, because that is what the session and the mood log need.
   */
  private normalise(raw: unknown): Classification {
    const entries = Array.isArray(raw) ? raw : [raw];
    const pairs: [string, number][] = [];
    for (const entry of entries) {
      if (typeof entry !== "object" || entry === null) {
        continue;
      }
      const record = entry as Record<string, unknown>;
      if (Array.isArray(record.label) && Array.isArray(record.score)) {
        const labels = record.label as unknown[];
        const scores = record.score as unknown[];
        for (const [index, label] of labels.entries()) {
          pairs.push([String(label), Number(scores[index] ?? 0)]);
        }
        continue;
      }
      if (typeof record.label === "string") {
        pairs.push([record.label, Number(record.score ?? 0)]);
      }
    }
    if (pairs.length === 0) {
      throw new ModelUnavailable(this.id, "the classifier returned no label");
    }
    const sorted = [...pairs].sort((left, right) => right[1] - left[1]);
    const [label, score] = sorted[0] ?? ["unknown", 0];
    const distribution: Record<string, number> = {};
    for (const [key, value] of sorted) {
      distribution[key] = Number.isFinite(value) ? value : 0;
    }
    return { label, score: Number.isFinite(score) ? score : 0, distribution };
  }
}

/** The emotion slot. */
export function createEmotionModel(options: TransformersOptions): EmotionModel {
  const classifier = new TransformersClassifier({ ...options, modelId: "emotion" });
  return {
    id: "emotion",
    capability: "classifiers",
    label: classifier.label,
    labels: options.entry.labels.length > 0 ? options.entry.labels : ["neutral"],
    get ready() {
      return classifier.ready;
    },
    init: (listener) => classifier.init(listener),
    dispose: () => classifier.dispose(),
    classify: async (text: string): Promise<EmotionResult> => {
      const result = await classifier.classify(text);
      return { ...result, source: "emotion" };
    },
  };
}

/**
 * The safety-signal slot.
 *
 * `raises_caution` is the only influence it has: the session may raise a level on it
 * and nothing anywhere may lower one.
 */
export function createSafetyModel(
  options: TransformersOptions,
  threshold = 0.5,
): SafetySignalModel {
  const classifier = new TransformersClassifier({ ...options, modelId: "safety", threshold });
  return {
    id: "safety",
    capability: "classifiers",
    label: classifier.label,
    threshold,
    get ready() {
      return classifier.ready;
    },
    init: (listener) => classifier.init(listener),
    dispose: () => classifier.dispose(),
    classify: async (text: string): Promise<SafetySignal> => {
      const result = await classifier.classify(text);
      return {
        label: result.label,
        score: result.score,
        raises_caution: result.score >= threshold,
        source: "safety",
      };
    },
  };
}
