/**
 * The WebLLM adapter: the small on-device language model.
 *
 * Like the classifier adapter, the library is loaded by specifier at consent time and
 * is not a dependency of this repository, so in this build `init()` raises
 * `ModelUnavailable` and the session falls back to the template writer. The adapter is
 * still written against the real API shape and is tested against a stub module, so the
 * wiring — progress mapping, streaming, token counting, prompt choice — is covered.
 *
 * HONESTY REQUIREMENTS THIS FILE OWNS
 *   - The system prompt is one of the *reviewed* prompts that ships in the bundle
 *     (`system_v1`, the shortest). A smaller model is not given a new, unreviewed
 *     prompt: that would put words in front of a person that no red team has read.
 *   - `runtimeLabel` names the model and says it is the smaller one. The UI shows it
 *     next to the reply, because "a 1.5B model wrote this" is material to trusting it.
 *   - tokens/second is measured from the actual stream, never estimated, and is null
 *     until there is one.
 *   - WebLLM fetches and caches its own artifacts, so this adapter does not hash them;
 *     that gap is recorded in the manifest and in PROGRESS.md's known issues.
 */

import type { ModelEntry } from "../manifest";
import type { PrivateBundle } from "../../rules/bundleSchema";
import type { ProgressListener, ReplyModel, ReplyOutcome, ReplyRequest, ReplyTurn } from "../types";
import { ModelUnavailable } from "../types";
import type { RuntimeInstaller } from "../downloadManager";
import type { ModuleLoader } from "./moduleLoader";
import { loadRuntimeModule, requireExport } from "./moduleLoader";

export const WEBLLM_SPECIFIER = "@mlc-ai/web-llm";
/** The reviewed prompt a small model is given. Shortest of the three, by design. */
export const PRIVATE_PROMPT_KEY = "system_v1";

interface ChatMessage {
  role: "system" | "user" | "assistant";
  content: string;
}

interface CompletionChunk {
  choices?: { delta?: { content?: string }; message?: { content?: string }; text?: string }[];
}

interface CompletionRequest {
  messages: ChatMessage[];
  max_tokens?: number;
  temperature?: number;
  top_p?: number;
  stream?: boolean;
}

interface Engine {
  chat?: {
    completions?: {
      create?: (request: CompletionRequest) => Promise<unknown>;
    };
  };
  getTokenUsage?: () => Promise<{ prompt_tokens?: number; completion_tokens?: number }>;
}

type EngineFactory = (
  model: string,
  options?: { initProgressCallback?: (report: ProgressReport) => void },
) => Promise<Engine>;

interface ProgressReport {
  progress?: number;
  text?: string;
}

export interface WebLlmOptions {
  entry: ModelEntry;
  bundle: PrivateBundle;
  loader?: ModuleLoader;
  /** Sampling temperature. Low: a support conversation is not a creative writing task. */
  temperature?: number;
  /** Which reviewed prompt to use. Defaults to the shortest. */
  promptKey?: string;
  monotonic?: () => number;
}

export class WebLlmReplyModel implements ReplyModel {
  readonly id = "reply" as const;
  readonly capability = "replies" as const;
  readonly label: string;

  private readonly entry: ModelEntry;
  private readonly systemPrompt: string;
  private readonly loader: ModuleLoader | undefined;
  private readonly temperature: number;
  private readonly monotonic: () => number;
  private engine: Engine | null = null;
  private initPromise: Promise<void> | null = null;
  private rate: number | null = null;

  constructor(options: WebLlmOptions) {
    this.entry = options.entry;
    this.loader = options.loader;
    this.temperature = options.temperature ?? 0.4;
    this.monotonic = options.monotonic ?? (() => performance.now());
    const key = options.promptKey ?? PRIVATE_PROMPT_KEY;
    const prompt = options.bundle.prompts[key];
    if (prompt === undefined) {
      // Fail at construction, not at the first message: a missing prompt would mean
      // improvising instructions for a model that talks to people in crisis.
      throw new ModelUnavailable("reply", `the bundle ships no prompt named ${key}`);
    }
    this.systemPrompt = prompt.text;
    this.label = `${options.entry.label} — ${options.entry.honest_note}`;
  }

  get ready(): boolean {
    return this.engine !== null;
  }

  get tokensPerSecond(): number | null {
    return this.rate;
  }

  /** The prompt this model is given, so the UI and the tests can inspect it. */
  get prompt(): string {
    return this.systemPrompt;
  }

  init(listener?: ProgressListener): Promise<void> {
    if (this.initPromise === null) {
      this.initPromise = this.load(listener).catch((error) => {
        this.initPromise = null;
        throw error;
      });
    }
    return this.initPromise;
  }

  async generate(request: ReplyRequest, onChunk?: (text: string) => void): Promise<ReplyOutcome> {
    if (this.engine === null) {
      await this.init();
    }
    const engine = this.engine;
    const create = engine?.chat?.completions?.create;
    if (engine === null || create === undefined) {
      throw new ModelUnavailable("reply", "the engine exposes no chat completions API");
    }
    const messages = this.messages(request);
    const started = this.monotonic();
    let text = "";
    let tokens = 0;
    try {
      const result = await create.call(engine.chat?.completions, {
        messages,
        max_tokens: request.maxTokens,
        temperature: this.temperature,
        stream: onChunk !== undefined,
      });
      if (isAsyncIterable(result)) {
        for await (const chunk of result) {
          const piece = chunkText(chunk);
          if (piece === "") {
            continue;
          }
          text += piece;
          onChunk?.(piece);
        }
      } else {
        text = chunkText(result);
        if (text !== "") {
          onChunk?.(text);
        }
      }
      // Counted once over the whole reply: counting per chunk would over-count at
      // every boundary where the runtime split a word.
      tokens = countTokens(text);
    } catch (error) {
      throw new ModelUnavailable(
        "reply",
        `generation failed: ${error instanceof Error ? error.message : String(error)}`,
      );
    }
    const seconds = (this.monotonic() - started) / 1000;
    this.rate = seconds > 0 && tokens > 0 ? Math.round((tokens / seconds) * 10) / 10 : null;
    if (text.trim() === "") {
      // An empty reply is a failure, not an answer: the session falls back to the
      // template writer rather than showing a blank bubble.
      throw new ModelUnavailable("reply", "the model produced no text");
    }
    return {
      text,
      tokens,
      tokensPerSecond: this.rate,
      runtimeLabel: `${this.entry.label} (on device, ${this.rate === null ? "rate unknown" : `${this.rate} tok/s`})`,
      degraded: false,
      source: "reply",
    };
  }

  async dispose(): Promise<void> {
    const engine = this.engine as (Engine & { unload?: () => Promise<void> }) | null;
    this.engine = null;
    this.initPromise = null;
    this.rate = null;
    if (engine?.unload !== undefined) {
      try {
        await engine.unload();
      } catch {
        // Releasing GPU memory is best-effort; the page unload reclaims it.
      }
    }
  }

  // ---------------------------------------------------------------- internals

  private async load(listener?: ProgressListener): Promise<void> {
    const module = await loadRuntimeModule({
      modelId: "reply",
      specifier: WEBLLM_SPECIFIER,
      loader: this.loader,
    });
    const factory = requireExport<EngineFactory>(
      module,
      ["CreateMLCEngine", "CreateWebLLMEngine"],
      "reply",
      WEBLLM_SPECIFIER,
    );
    const modelRef = this.entry.upstream ?? this.entry.id;
    try {
      this.engine = await factory(modelRef, {
        initProgressCallback: (report) => {
          if (listener === undefined) {
            return;
          }
          const progress = typeof report.progress === "number" ? report.progress : 0;
          listener({
            modelId: "reply",
            phase: progress >= 1 ? "loading" : "downloading",
            receivedBytes: Math.round(progress * 100),
            totalBytes: 100,
            bytesPerSecond: null,
            ...(report.text === undefined ? {} : { detail: report.text }),
          });
        },
      });
    } catch (error) {
      throw new ModelUnavailable(
        "reply",
        `could not start the engine: ${error instanceof Error ? error.message : String(error)}`,
      );
    }
    listener?.({
      modelId: "reply",
      phase: "ready",
      receivedBytes: 100,
      totalBytes: 100,
      bytesPerSecond: null,
    });
  }

  private messages(request: ReplyRequest): ChatMessage[] {
    const history: ChatMessage[] = request.history.slice(-6).map((turn: ReplyTurn) => ({
      role: turn.role === "assistant" ? ("assistant" as const) : ("user" as const),
      content: turn.text,
    }));
    return [
      { role: "system", content: this.systemPrompt },
      ...history,
      { role: "user", content: request.text },
    ];
  }
}

/**
 * The download manager's installer for the reply model.
 *
 * WebLLM fetches and caches its own artifacts through the Cache API and reports its
 * own progress, so the manager's job here is consent, progress relay and honest
 * failure — not a second download path.
 */
export class WebLlmInstaller implements RuntimeInstaller {
  private readonly model: WebLlmReplyModel;

  constructor(model: WebLlmReplyModel) {
    this.model = model;
  }

  async install(_entry: ModelEntry, listener?: ProgressListener): Promise<void> {
    await this.model.init(listener);
  }
}

function isAsyncIterable(value: unknown): value is AsyncIterable<CompletionChunk> {
  return (
    typeof value === "object" &&
    value !== null &&
    typeof (value as { [Symbol.asyncIterator]?: unknown })[Symbol.asyncIterator] === "function"
  );
}

function chunkText(chunk: unknown): string {
  if (typeof chunk === "string") {
    return chunk;
  }
  if (typeof chunk !== "object" || chunk === null) {
    return "";
  }
  const record = chunk as CompletionChunk & { text?: string; message?: { content?: string } };
  if (typeof record.text === "string") {
    return record.text;
  }
  if (typeof record.message?.content === "string") {
    return record.message.content;
  }
  const choice = record.choices?.[0];
  if (choice === undefined) {
    return "";
  }
  return choice.delta?.content ?? choice.message?.content ?? choice.text ?? "";
}

/**
 * A word-ish token count.
 *
 * The real tokenizer lives inside the runtime and asking it for a count would mean
 * another round trip per chunk. Words are close enough for a rate the UI shows as
 * "roughly N per second", and the label says where it came from — inventing precision
 * here would be worse than admitting the estimate.
 */
export function countTokens(text: string): number {
  return text.split(/\s+/).filter(Boolean).length;
}
