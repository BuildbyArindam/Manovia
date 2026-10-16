/**
 * The model layer's interfaces.
 *
 * Every external dependency sits behind an interface with a Fake (AGENTS.md), and
 * Private Mode needs that more than most: the real runtimes are WebGPU/WASM
 * libraries that cannot be installed or exercised in an offline sandbox, and the
 * tests still have to cover the whole session — assessment, reply, guard, storage.
 *
 * Three runtimes, in ascending order of cost:
 *
 *   EmotionModel      distilled ONNX classifier (transformers.js / ONNX Runtime Web)
 *   SafetySignalModel distilled ONNX classifier for crisis-adjacent language
 *   ReplyModel        small open LLM (WebLLM over WebGPU), or the template writer
 *
 * Two rules shape these interfaces:
 *
 * 1. **Models only ever raise caution.** `SafetySignal.raises_caution` is the only
 *    thing a classifier may contribute to the risk level; the deterministic rules
 *    engine decides it (AGENTS.md ML ground rules). There is no `lowers` field, so
 *    a model cannot talk the app out of a crisis response.
 * 2. **Progress is reported, not polled.** Downloads and model loads take minutes on
 *    a real device, so every `init` takes a listener and every byte is accounted
 *    for; the UI shows size, speed and an honest "this is the smaller model" label.
 */

/** The three runtime slots. `safety` is a signal, never the decision. */
export type ModelId = "emotion" | "safety" | "reply";

/** Which capability tier a model needs (see runtime/capability.ts). */
export type ModelCapability = "classifiers" | "replies";

/** Where a model is in its lifecycle. Shown verbatim in the manager UI. */
export type ModelPhase =
  | "not-downloaded"
  | "downloading"
  | "paused"
  | "verifying"
  | "loading"
  | "ready"
  | "failed"
  | "deleted";

export interface ProgressUpdate {
  modelId: ModelId;
  phase: ModelPhase;
  /** Bytes of this model that are on the device. */
  receivedBytes: number;
  /** Bytes expected. 0 while unknown. */
  totalBytes: number;
  /** Smoothed rate; null before enough samples exist. */
  bytesPerSecond: number | null;
  /** File being fetched, when the model has several. */
  file?: string;
  /** Human-readable detail for a failure. Never includes model output text. */
  detail?: string;
}

export type ProgressListener = (update: ProgressUpdate) => void;

/** One label plus its confidence, and the whole distribution for the mood log. */
export interface EmotionResult {
  label: string;
  score: number;
  distribution: Record<string, number>;
  /** Which runtime produced it, so the UI can say "on-device". */
  source: ModelId | "none";
}

/**
 * A classifier's contribution to safety. It may only add caution.
 *
 * `raises_caution` is true when the score passes the model's own threshold; the
 * session then re-runs nothing and lowers nothing — it records the signal and lets
 * the rules engine's level stand (or be raised by the session-risk aggregator).
 */
export interface SafetySignal {
  label: string;
  score: number;
  raises_caution: boolean;
  source: ModelId | "none";
}

export interface ReplyTurn {
  role: "user" | "assistant";
  text: string;
}

export interface ReplyRequest {
  /** The user's message. Stays on the device. */
  text: string;
  /** What the rules engine decided — the reply model is told, never asked. */
  level: string;
  /** Rendered crisis message, when the level is HIGH or IMMINENT. */
  crisisMessage?: string | null;
  locale: string;
  history: ReplyTurn[];
  maxTokens: number;
}

export interface ReplyOutcome {
  /** The raw model text. It has not passed the output guard yet. */
  text: string;
  tokens: number;
  tokensPerSecond: number | null;
  /** Honest label for the UI: which runtime wrote this. */
  runtimeLabel: string;
  /** True when the reply came from a template because a model was unavailable. */
  degraded: boolean;
  /** Which slot produced it. */
  source: ModelId | "template";
}

/** Common lifecycle for all three runtimes. */
export interface ModelRuntime {
  readonly id: ModelId;
  readonly capability: ModelCapability;
  /** What the UI calls it. Never implies parity with the server's models. */
  readonly label: string;
  readonly ready: boolean;
  /** Download (if needed) and load. Reports progress; safe to call once. */
  init(listener?: ProgressListener): Promise<void>;
  dispose(): Promise<void>;
}

export interface EmotionModel extends ModelRuntime {
  readonly id: "emotion";
  classify(text: string): Promise<EmotionResult>;
  /** The labels this model can produce, for the mood log's colour scale. */
  readonly labels: readonly string[];
}

export interface SafetySignalModel extends ModelRuntime {
  readonly id: "safety";
  classify(text: string): Promise<SafetySignal>;
  /** The score at which the model asks for more caution. */
  readonly threshold: number;
}

export interface ReplyModel extends ModelRuntime {
  readonly id: "reply";
  /**
   * Produce a reply. `onChunk` may be called many times; the returned outcome
   * carries the full text. Streaming matters because a 1.5B model on a laptop GPU
   * takes seconds, and a user staring at a spinner during a crisis message is worse
   * than a user watching words arrive.
   */
  generate(request: ReplyRequest, onChunk?: (text: string) => void): Promise<ReplyOutcome>;
  /** Measured over the last generate() call; null before the first. */
  readonly tokensPerSecond: number | null;
}

/** Raised when a model cannot run here. The session catches it and degrades. */
export class ModelUnavailable extends Error {
  readonly modelId: ModelId;
  readonly reason: string;

  constructor(modelId: ModelId, reason: string) {
    super(`model ${modelId} is unavailable: ${reason}`);
    this.name = "ModelUnavailable";
    this.modelId = modelId;
    this.reason = reason;
  }
}
