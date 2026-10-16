/**
 * The runtime adapters, tested against stub modules.
 *
 * `@huggingface/transformers` and `@mlc-ai/web-llm` are not installed here (see
 * `moduleLoader.ts` for why that is deliberate), so these tests inject a loader that
 * returns a module with the real API *shape*. What that covers is everything the
 * adapters are responsible for: reading progress, normalising whatever output shape
 * the runtime produced, streaming, counting tokens, choosing the reviewed prompt, and
 * failing as `ModelUnavailable` — which is what lets the session degrade instead of
 * showing an error to somebody mid-crisis.
 *
 * What it cannot cover is the real inference. That is on the manual checklist for a
 * device with WebGPU.
 */

import { describe, expect, it } from "vitest";

import {
  LOCAL_MODEL_PATH,
  TRANSFORMERS_SPECIFIER,
  TransformersClassifier,
  createEmotionModel,
  createSafetyModel,
} from "./transformers";
import {
  PRIVATE_PROMPT_KEY,
  WEBLLM_SPECIFIER,
  WebLlmInstaller,
  WebLlmReplyModel,
  countTokens,
} from "./webllm";
import type { ModuleLoader } from "./moduleLoader";
import { ModelUnavailable } from "../types";
import type { ProgressUpdate, ReplyRequest } from "../types";
import type { ModelEntry } from "../manifest";
import { fixtureManifest } from "../../testSupport/modelFakes";
import { InMemorySink } from "../downloadManager";
import { readBundle } from "../../testSupport/repoArtifacts";

const bundle = readBundle();

function entry(overrides: Partial<ModelEntry> = {}): ModelEntry {
  return {
    id: "emotion",
    kind: "sequence-classification",
    runtime: "transformers",
    capability: "classifiers",
    status: "available",
    label: "Distilled emotion classifier (on-device)",
    honest_note: "a distilled model, less accurate than the server's",
    upstream: "https://models.invalid/emotion",
    licence: "MIT",
    managed_by_runtime: false,
    labels: ["neutral", "sadness", "joy"],
    files: [],
    total_bytes: 26_000_000,
    ...overrides,
  };
}

function replyEntry(): ModelEntry {
  return entry({
    id: "reply",
    kind: "causal-lm",
    runtime: "webllm",
    capability: "replies",
    status: "available",
    label: "Small on-device language model (WebGPU)",
    honest_note: "much smaller than the server model: shorter, plainer replies",
    managed_by_runtime: true,
    labels: [],
  });
}

interface StubPipeline {
  calls: string[];
  disposed: number;
}

/** A loader that returns a `pipeline` factory with scripted behaviour. */
function transformersLoader(options: {
  output?: unknown;
  outputs?: unknown[];
  progress?: {
    status?: string;
    file?: string;
    loaded?: number;
    total?: number;
    progress?: number;
  }[];
  fail?: Error;
  env?: Record<string, unknown> | null;
  record?: StubPipeline;
}): ModuleLoader {
  return async (specifier: string) => {
    expect(specifier).toBe(TRANSFORMERS_SPECIFIER);
    const record: StubPipeline = options.record ?? { calls: [], disposed: 0 };
    let index = 0;
    const pipeline = async (
      task: string,
      model: string,
      factory?: { progress_callback?: (item: unknown) => void },
    ) => {
      expect(task).toBe("text-classification");
      expect(model).toBe("https://models.invalid/emotion");
      for (const item of options.progress ?? []) {
        factory?.progress_callback?.(item);
      }
      // The adapter disposes the *pipeline object*, so `dispose` belongs there, not
      // on the factory.
      const built = async (text: string) => {
        record.calls.push(text);
        if (options.fail !== undefined) {
          throw options.fail;
        }
        const outputs = options.outputs;
        if (outputs !== undefined) {
          const value = outputs[Math.min(index, outputs.length - 1)];
          index += 1;
          return value;
        }
        return options.output ?? { label: "sadness", score: 0.82 };
      };
      built.dispose = async () => {
        record.disposed += 1;
      };
      return built;
    };
    return {
      pipeline,
      ...(options.env === null ? {} : { env: options.env ?? {} }),
    };
  };
}

interface StubEngine {
  requests: {
    messages: { role: string; content: string }[];
    stream?: boolean;
    max_tokens?: number;
  }[];
  unloaded: number;
}

function webllmLoader(options: {
  chunks?: string[];
  reply?: unknown;
  progress?: { progress?: number; text?: string }[];
  fail?: Error;
  record?: StubEngine;
  noExports?: boolean;
}): ModuleLoader {
  return async (specifier: string) => {
    expect(specifier).toBe(WEBLLM_SPECIFIER);
    if (options.noExports ?? false) {
      return { somethingElse: () => Promise.resolve({}) };
    }
    const record: StubEngine = options.record ?? { requests: [], unloaded: 0 };
    const CreateMLCEngine = async (
      model: string,
      factory?: { initProgressCallback?: (report: unknown) => void },
    ) => {
      expect(model).toBe("https://models.invalid/emotion");
      for (const report of options.progress ?? []) {
        factory?.initProgressCallback?.(report);
      }
      if (options.fail !== undefined) {
        throw options.fail;
      }
      return {
        chat: {
          completions: {
            create: async (request: {
              messages: { role: string; content: string }[];
              stream?: boolean;
              max_tokens?: number;
            }) => {
              record.requests.push(request);
              if (options.reply !== undefined) {
                return options.reply;
              }
              const chunks = options.chunks ?? ["That sounds ", "heavy. ", "How are you?"];
              return (async function* stream() {
                for (const chunk of chunks) {
                  yield { choices: [{ delta: { content: chunk } }] };
                }
              })();
            },
          },
        },
        unload: async () => {
          record.unloaded += 1;
        },
      };
    };
    return { CreateMLCEngine };
  };
}

function replyRequest(overrides: Partial<ReplyRequest> = {}): ReplyRequest {
  return {
    text: "work has been hard",
    level: "none",
    locale: "en",
    history: [],
    maxTokens: 120,
    ...overrides,
  };
}

describe("the classifiers adapter", () => {
  it("names the library when it is not in this build", async () => {
    const model = new TransformersClassifier({
      modelId: "emotion",
      entry: entry(),
      loader: async () => {
        throw new Error("Cannot find module");
      },
    });
    await expect(model.init()).rejects.toThrow(ModelUnavailable);
    await expect(model.init()).rejects.toMatchObject({ modelId: "emotion" });
    await expect(model.init()).rejects.toThrow(/@huggingface\/transformers is not available/);
    expect(model.ready).toBe(false);
  });

  it("names the missing export when the runtime API has moved", async () => {
    const model = new TransformersClassifier({
      modelId: "safety",
      entry: entry({ id: "safety" }),
      loader: async () => ({ notPipeline: true }),
    });
    await expect(model.init()).rejects.toThrow(/exported none of pipeline/);
  });

  it("maps the runtime's progress onto the manager's phases", async () => {
    const updates: ProgressUpdate[] = [];
    const model = new TransformersClassifier({
      modelId: "emotion",
      entry: entry(),
      loader: transformersLoader({
        progress: [
          { status: "initiate", file: "model_quantized.onnx", total: 26_000_000 },
          {
            status: "progress",
            file: "model_quantized.onnx",
            loaded: 13_000_000,
            total: 26_000_000,
            progress: 50,
          },
          { status: "done", file: "model_quantized.onnx", loaded: 26_000_000, total: 26_000_000 },
        ],
      }),
    });
    await model.init((update) => updates.push(update));
    expect(updates.map((update) => update.phase)).toEqual([
      "loading",
      "downloading",
      "downloading",
      "loading",
      "ready",
    ]);
    expect(updates[2]?.receivedBytes).toBe(13_000_000);
    expect(updates[2]?.totalBytes).toBe(26_000_000);
    expect(updates[2]?.file).toBe("model_quantized.onnx");
    expect(updates[2]?.detail).toBe("50% of model_quantized.onnx");
    expect(model.ready).toBe(true);
  });

  it("reduces every output shape the runtime is known to produce", async () => {
    const shapes: unknown[] = [
      { label: "sadness", score: 0.82 },
      [
        { label: "sadness", score: 0.7 },
        { label: "neutral", score: 0.2 },
      ],
      { label: ["joy", "neutral"], score: [0.6, 0.4] },
    ];
    const model = new TransformersClassifier({
      modelId: "emotion",
      entry: entry(),
      loader: transformersLoader({ outputs: shapes }),
    });
    await model.init();
    expect(await model.classify("first")).toMatchObject({ label: "sadness", score: 0.82 });
    const second = await model.classify("second");
    expect(second.label).toBe("sadness");
    expect(second.distribution).toEqual({ sadness: 0.7, neutral: 0.2 });
    const third = await model.classify("third");
    expect(third.label).toBe("joy");
    expect(third.distribution).toEqual({ joy: 0.6, neutral: 0.4 });
  });

  it("refuses to invent a label when the runtime returns nothing", async () => {
    const model = new TransformersClassifier({
      modelId: "emotion",
      entry: entry(),
      loader: transformersLoader({ output: [] }),
    });
    await model.init();
    await expect(model.classify("anything")).rejects.toThrow(/returned no label/);
  });

  it("turns an inference failure into ModelUnavailable, so the session can degrade", async () => {
    const model = new TransformersClassifier({
      modelId: "emotion",
      entry: entry(),
      loader: transformersLoader({ fail: new Error("wasm memory exhausted") }),
    });
    await model.init();
    await expect(model.classify("anything")).rejects.toThrow(/inference failed/);
    await expect(model.classify("anything")).rejects.toThrow(/wasm memory exhausted/);
  });

  it("runs the verified local files when a sink is supplied", async () => {
    const env: Record<string, unknown> = { allowRemoteModels: true, localModelPath: "/" };
    const model = new TransformersClassifier({
      modelId: "emotion",
      entry: entry(),
      sink: new InMemorySink(),
      loader: transformersLoader({ env }),
    });
    await model.init();
    expect(env.allowRemoteModels).toBe(false);
    expect(env.localModelPath).toBe(LOCAL_MODEL_PATH);
    expect(model.usesVerifiedLocalFiles).toBe(true);
  });

  it("says so when the runtime has no local-model switch, rather than implying verified files", async () => {
    const updates: ProgressUpdate[] = [];
    const model = new TransformersClassifier({
      modelId: "emotion",
      entry: entry(),
      sink: new InMemorySink(),
      loader: transformersLoader({ env: { somethingElse: true } }),
    });
    await model.init((update) => updates.push(update));
    expect(model.usesVerifiedLocalFiles).toBe(false);
    expect(updates.map((update) => update.detail).join()).toMatch(/will fetch upstream/);
  });

  it("builds the pipeline once, however many callers ask", async () => {
    const record: StubPipeline = { calls: [], disposed: 0 };
    const inner = transformersLoader({ record });
    let loads = 0;
    const model = new TransformersClassifier({
      modelId: "emotion",
      entry: entry(),
      loader: async (specifier) => {
        loads += 1;
        return inner(specifier);
      },
    });
    await Promise.all([model.init(), model.init(), model.init()]);
    // One load, one pipeline: two of them would download a model twice.
    expect(loads).toBe(1);
    await model.classify("one");
    expect(record.calls).toEqual(["one"]);
  });

  it("releases the pipeline on dispose", async () => {
    const record: StubPipeline = { calls: [], disposed: 0 };
    const model = new TransformersClassifier({
      modelId: "emotion",
      entry: entry(),
      loader: transformersLoader({ record }),
    });
    await model.init();
    await model.dispose();
    expect(record.disposed).toBe(1);
    expect(model.ready).toBe(false);
  });

  it("fills the emotion slot with the manifest's labels", async () => {
    const model = createEmotionModel({
      modelId: "emotion",
      entry: entry(),
      loader: transformersLoader({}),
    });
    expect(model.id).toBe("emotion");
    expect(model.labels).toEqual(["neutral", "sadness", "joy"]);
    expect(model.label).toMatch(/distilled/i);
    await model.init();
    const result = await model.classify("I feel low");
    expect(result.source).toBe("emotion");
    expect(result.label).toBe("sadness");
  });

  it("fills the safety slot, and only ever asks for more caution", async () => {
    const model = createSafetyModel(
      {
        modelId: "safety",
        entry: entry({ id: "safety" }),
        loader: transformersLoader({
          outputs: [
            { label: "crisis", score: 0.91 },
            { label: "calm", score: 0.2 },
          ],
        }),
      },
      0.5,
    );
    expect(model.id).toBe("safety");
    expect(model.threshold).toBe(0.5);
    await model.init();
    const high = await model.classify("first");
    expect(high).toMatchObject({ label: "crisis", raises_caution: true, source: "safety" });
    const low = await model.classify("second");
    expect(low.raises_caution).toBe(false);
    // There is no field a classifier could use to lower a level.
    expect(Object.keys(high).sort()).toEqual(["label", "raises_caution", "score", "source"]);
  });
});

describe("the reply model adapter", () => {
  it("names the library when it is not in this build", async () => {
    const model = new WebLlmReplyModel({
      entry: replyEntry(),
      bundle,
      loader: async () => {
        throw new Error("Cannot find module");
      },
    });
    await expect(model.init()).rejects.toThrow(/@mlc-ai\/web-llm is not available/);
  });

  it("names the missing factory when the runtime API has moved", async () => {
    const model = new WebLlmReplyModel({
      entry: replyEntry(),
      bundle,
      loader: webllmLoader({ noExports: true }),
    });
    await expect(model.init()).rejects.toThrow(/CreateMLCEngine, CreateWebLLMEngine/);
  });

  it("is given the reviewed prompt that ships in the bundle, and refuses to improvise one", () => {
    const model = new WebLlmReplyModel({ entry: replyEntry(), bundle });
    expect(model.prompt).toBe(bundle.prompts[PRIVATE_PROMPT_KEY]?.text);
    expect(model.prompt.length).toBeGreaterThan(500);
    expect(
      () => new WebLlmReplyModel({ entry: replyEntry(), bundle, promptKey: "system_v9" }),
    ).toThrow(ModelUnavailable);
  });

  it("streams chunks, counts tokens and measures the rate", async () => {
    const chunks: string[] = [];
    let tick = 0;
    const model = new WebLlmReplyModel({
      entry: replyEntry(),
      bundle,
      loader: webllmLoader({ chunks: ["That sounds ", "heavy. ", "How are you?"] }),
      monotonic: () => (tick += 1000),
    });
    const outcome = await model.generate(replyRequest(), (text) => chunks.push(text));
    expect(chunks).toEqual(["That sounds ", "heavy. ", "How are you?"]);
    expect(outcome.text).toBe("That sounds heavy. How are you?");
    expect(outcome.degraded).toBe(false);
    expect(outcome.source).toBe("reply");
    expect(outcome.tokens).toBe(countTokens(outcome.text));
    expect(outcome.tokens).toBe(6);
    expect(outcome.tokensPerSecond).not.toBeNull();
    expect(outcome.runtimeLabel).toMatch(/on device/);
    expect(outcome.runtimeLabel).toMatch(/tok\/s/);
    expect(model.tokensPerSecond).toBe(outcome.tokensPerSecond);
  });

  it("handles a runtime that answers in one piece instead of streaming", async () => {
    const model = new WebLlmReplyModel({
      entry: replyEntry(),
      bundle,
      loader: webllmLoader({
        reply: { choices: [{ message: { content: "I'm here. Tell me more?" } }] },
      }),
    });
    const chunks: string[] = [];
    const outcome = await model.generate(replyRequest(), (text) => chunks.push(text));
    expect(outcome.text).toBe("I'm here. Tell me more?");
    expect(chunks).toEqual(["I'm here. Tell me more?"]);
  });

  it("treats an empty reply as a failure, not as an answer", async () => {
    const model = new WebLlmReplyModel({
      entry: replyEntry(),
      bundle,
      loader: webllmLoader({ chunks: [] }),
    });
    await expect(model.generate(replyRequest())).rejects.toThrow(/produced no text/);
  });

  it("sends the system prompt, a bounded history and the message — nothing else", async () => {
    const record: StubEngine = { requests: [], unloaded: 0 };
    const model = new WebLlmReplyModel({
      entry: replyEntry(),
      bundle,
      loader: webllmLoader({ record }),
    });
    const history = Array.from({ length: 10 }, (_unused, index) => ({
      role: index % 2 === 0 ? ("user" as const) : ("assistant" as const),
      text: `turn ${index}`,
    }));
    await model.generate(replyRequest({ history, maxTokens: 90 }));
    expect(record.requests).toHaveLength(1);
    const messages = record.requests[0]?.messages ?? [];
    expect(messages[0]?.role).toBe("system");
    expect(messages[0]?.content).toBe(bundle.prompts[PRIVATE_PROMPT_KEY]?.text);
    expect(messages).toHaveLength(8);
    expect(messages.at(-1)?.content).toBe("work has been hard");
    expect(record.requests[0]?.max_tokens).toBe(90);
    // Streaming is only asked for when somebody is listening for chunks.
    expect(record.requests[0]?.stream).toBe(false);
    await model.generate(replyRequest({ history }), () => undefined);
    expect(record.requests[1]?.stream).toBe(true);
    // No region, no user id, no telemetry field: the request carries only words.
    expect(JSON.stringify(record.requests[0])).not.toMatch(/region|user_id|telemetry/i);
  });

  it("maps engine progress onto the manager's phases", async () => {
    const updates: ProgressUpdate[] = [];
    const model = new WebLlmReplyModel({
      entry: replyEntry(),
      bundle,
      loader: webllmLoader({
        progress: [
          { progress: 0.2, text: "fetching params" },
          { progress: 0.8, text: "compiling shaders" },
          { progress: 1 },
        ],
      }),
    });
    await model.init((update) => updates.push(update));
    expect(updates.map((update) => update.phase)).toEqual([
      "downloading",
      "downloading",
      "loading",
      "ready",
    ]);
    expect(updates[0]?.detail).toBe("fetching params");
    expect(model.ready).toBe(true);
  });

  it("reports an engine that will not start", async () => {
    const model = new WebLlmReplyModel({
      entry: replyEntry(),
      bundle,
      loader: webllmLoader({ fail: new Error("WebGPU not supported") }),
    });
    await expect(model.init()).rejects.toThrow(/could not start the engine/);
    await expect(model.init()).rejects.toThrow(/WebGPU not supported/);
  });

  it("unloads the engine on dispose", async () => {
    const record: StubEngine = { requests: [], unloaded: 0 };
    const model = new WebLlmReplyModel({
      entry: replyEntry(),
      bundle,
      loader: webllmLoader({ record }),
    });
    await model.init();
    await model.dispose();
    expect(record.unloaded).toBe(1);
    expect(model.ready).toBe(false);
    expect(model.tokensPerSecond).toBeNull();
  });

  it("lets the download manager drive the same load", async () => {
    const updates: ProgressUpdate[] = [];
    const model = new WebLlmReplyModel({
      entry: replyEntry(),
      bundle,
      loader: webllmLoader({ progress: [{ progress: 0.5 }] }),
    });
    const installer = new WebLlmInstaller(model);
    await installer.install(replyEntry(), (update) => updates.push(update));
    expect(model.ready).toBe(true);
    expect(updates.some((update) => update.phase === "ready")).toBe(true);
  });

  it("refuses rather than inventing a model reference the build never named", async () => {
    // `PRIVATE_LLM_MODEL_ID` unset means the manifest's reply slot has no upstream.
    // Falling back to the entry's own id would ask WebLLM for a model called
    // "reply", which would either fail obscurely or — worse — resolve to something
    // nobody chose. The committed manifest is in exactly this state.
    const model = new WebLlmReplyModel({
      entry: { ...replyEntry(), status: "planned", upstream: null },
      bundle,
      loader: webllmLoader({ chunks: ["never reached"] }),
    });
    await expect(model.generate(replyRequest())).rejects.toThrow(
      /does not name an on-device reply model/,
    );
    await expect(model.generate(replyRequest())).rejects.toMatchObject({ modelId: "reply" });
  });

  it("is wired to the manifest's reply entry, which the manager treats as runtime-managed", async () => {
    const manifest = await fixtureManifest([], { runtimeManaged: ["reply"] });
    const shipped = manifest.models.find((model) => model.id === "reply");
    expect(shipped?.managed_by_runtime).toBe(true);
    const model = new WebLlmReplyModel({
      entry: replyEntry(),
      bundle,
      loader: webllmLoader({}),
    });
    expect(model.id).toBe("reply");
    expect(model.capability).toBe("replies");
  });
});
