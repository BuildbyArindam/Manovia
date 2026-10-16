/**
 * Capability detection: can this device run Private Mode, and how much of it?
 *
 * Private Mode is not one feature but three, with very different hardware costs:
 *
 *   rules      the deterministic safety layer — a 39 KB gzipped JSON bundle and a
 *              regex engine. Runs anywhere JavaScript runs. This is the floor.
 *   classifiers the distilled ONNX emotion/safety models under ONNX Runtime Web.
 *              Needs WebAssembly (SIMD makes them ~4x faster) and a few hundred MB.
 *   replies    a small open LLM under WebLLM. Needs WebGPU and, realistically, 4 GB
 *              of memory and 4 cores.
 *
 * So the answer is a tier, not a yes/no, and every downgrade names its reason in a
 * closed vocabulary so the UI can explain it honestly instead of saying "not
 * supported". AGENTS.md rule: never promise what the device cannot do. When the
 * device cannot run the models, the app says so and offers standard mode — it does
 * not quietly fall back to a server, because that would move message text off the
 * device, which is the one thing Private Mode promises not to do.
 *
 * `collectEnvironment()` reads the browser; `assess()` is pure over that report so
 * the whole decision table is testable with fixtures, including the devices this
 * sandbox does not have (WebGPU in particular).
 */

/** What the browser reports about this device. Plain data, so tests can supply it. */
export interface EnvironmentReport {
  /** `window.isSecureContext` — WebCrypto, Cache API and service workers need it. */
  secureContext: boolean;
  /** Needed for `SharedArrayBuffer`, which ONNX Runtime Web uses for threads. */
  crossOriginIsolated: boolean;
  webgpu: boolean;
  /** Adapter description when WebGPU is present, else null. Never a device id. */
  webgpuAdapterName: string | null;
  /** Why WebGPU is missing, when the probe produced a reason. */
  webgpuError: string | null;
  wasm: boolean;
  wasmSimd: boolean;
  wasmThreads: boolean;
  hardwareConcurrency: number | null;
  /** `navigator.deviceMemory`, in GiB, rounded down to a power of two by Chrome. */
  deviceMemoryGb: number | null;
  /** From `navigator.storage.estimate()`; null when unavailable. */
  storageQuotaBytes: number | null;
  storageUsageBytes: number | null;
  indexedDb: boolean;
  cacheApi: boolean;
  webCrypto: boolean;
  serviceWorker: boolean;
  /** Coarse, from the UA. Used only for wording, never for a decision. */
  isMobile: boolean;
  platform: string;
}

/** A reason code. Closed vocabulary: the UI translates these, tests assert them. */
export type CapabilityCode =
  | "webgpu"
  | "wasm"
  | "wasm-simd"
  | "wasm-threads"
  | "memory"
  | "cores"
  | "storage"
  | "indexeddb"
  | "cache-api"
  | "webcrypto"
  | "service-worker"
  | "secure-context";

/** How much of Private Mode this device can run. */
export type CapabilityTier = "full" | "classifiers" | "rules-only";

export interface CapabilityPolicy {
  /** Below this, the LLM is not offered. WebLLM's own guidance is 4. */
  minCoresForLlm: number;
  /** Below this, the LLM is not offered. `deviceMemory` is capped at 8 by Chrome. */
  minMemoryGbForLlm: number;
  /** Below this, ONNX models are not offered. */
  minCoresForClassifiers: number;
  /** Free bytes needed before offering to download anything. */
  minFreeBytes: number;
  /** WebGPU is required for the reply model; SIMD is required for sane ONNX speed. */
  requireSimdForClassifiers: boolean;
}

export const DEFAULT_CAPABILITY_POLICY: CapabilityPolicy = {
  minCoresForLlm: 4,
  minMemoryGbForLlm: 4,
  minCoresForClassifiers: 2,
  // 3 GB: the reply model is ~1-2 GB quantised and the classifiers ~400 MB, and a
  // download that fills the disk leaves the browser evicting the cache mid-session.
  minFreeBytes: 3 * 1024 * 1024 * 1024,
  requireSimdForClassifiers: true,
};

export interface CapabilityAssessment {
  tier: CapabilityTier;
  /** What can actually run. The UI offers only these. */
  supported: { classifiers: boolean; replies: boolean; encryptedStorage: boolean };
  /** Reasons that stopped something. Each maps to one sentence of honest copy. */
  blocking: CapabilityCode[];
  /** Things that will work but slowly, or that reduce what is offered. */
  advisories: CapabilityCode[];
  /** Plain-language explanation for the panel. Never blames the user. */
  explanation: string;
  /** True when standard mode should be offered as the alternative. */
  offerStandardMode: boolean;
  /** True when Private Mode can still be used at all (rules only is still useful). */
  usable: boolean;
}

const GB = 1024 * 1024 * 1024;

/**
 * The WASM SIMD feature probe: the smallest module that uses `v128.const`.
 * `WebAssembly.validate` returns false on an engine without SIMD rather than
 * throwing, which is exactly the shape of answer needed here.
 */
const SIMD_PROBE = new Uint8Array([
  0, 97, 115, 109, 1, 0, 0, 0, 1, 5, 1, 96, 0, 1, 123, 3, 2, 1, 0, 10, 10, 1, 8, 0, 65, 0, 253, 15,
  253, 98, 11,
]);

/** A module that imports `env.memory` as shared, which needs threads support. */
const THREADS_PROBE = new Uint8Array([
  0, 97, 115, 109, 1, 0, 0, 0, 1, 4, 1, 96, 0, 0, 3, 2, 1, 0, 5, 4, 1, 3, 1, 1, 10, 11, 1, 9, 0, 65,
  0, 252, 26, 0, 26, 11,
]);

function has(feature: unknown): boolean {
  return feature !== undefined && feature !== null;
}

/**
 * A browser feature that may live on `globalThis` rather than on the `window` the
 * caller passed in. In a real browser they are the same object; in a test
 * environment a polyfill (fake-indexeddb) installs the global only.
 */
function feature(name: string): unknown {
  return (globalThis as Record<string, unknown>)[name];
}

function probeWasm(): { wasm: boolean; wasmSimd: boolean; wasmThreads: boolean } {
  if (typeof WebAssembly === "undefined" || !has(WebAssembly.validate)) {
    return { wasm: false, wasmSimd: false, wasmThreads: false };
  }
  try {
    const wasm = WebAssembly.validate(new Uint8Array([0, 97, 115, 109, 1, 0, 0, 0]));
    return {
      wasm,
      wasmSimd: wasm && WebAssembly.validate(SIMD_PROBE),
      wasmThreads: wasm && WebAssembly.validate(THREADS_PROBE),
    };
  } catch {
    return { wasm: false, wasmSimd: false, wasmThreads: false };
  }
}

interface WebGpuProbe {
  webgpu: boolean;
  webgpuAdapterName: string | null;
  webgpuError: string | null;
}

/**
 * Ask for a GPU adapter. This is the only honest WebGPU probe: feature flags and
 * UA strings both lie, and `navigator.gpu` can exist while every adapter request
 * fails (no driver, blocklisted, or a sandbox like this one).
 */
export async function probeWebgpu(nav?: Navigator): Promise<WebGpuProbe> {
  // `navigator.gpu` is not in TypeScript's DOM lib yet, hence the narrow cast: the
  // only member touched is `requestAdapter`, and only after checking it is callable.
  const navigatorWithGpu = (nav ?? globalThis.navigator) as
    ({ gpu?: { requestAdapter?: () => Promise<unknown> } } & Navigator) | undefined;
  const gpu = navigatorWithGpu?.gpu;
  if (!has(gpu) || typeof gpu?.requestAdapter !== "function") {
    return {
      webgpu: false,
      webgpuAdapterName: null,
      webgpuError: gpu ? "no-request-adapter" : "navigator.gpu is not available",
    };
  }
  try {
    const adapter = (await gpu.requestAdapter()) as {
      info?: { description?: string; vendor?: string };
      requestAdapterInfo?: () => unknown;
    } | null;
    if (!adapter) {
      return {
        webgpu: false,
        webgpuAdapterName: null,
        webgpuError: "no adapter (driver missing, blocklisted, or unsupported)",
      };
    }
    const info = adapter.info;
    const name = [info?.vendor, info?.description].filter(Boolean).join(" ").trim();
    return {
      webgpu: true,
      webgpuAdapterName: name === "" ? "unknown adapter" : name,
      webgpuError: null,
    };
  } catch (error) {
    return {
      webgpu: false,
      webgpuAdapterName: null,
      webgpuError: error instanceof Error ? error.message : String(error),
    };
  }
}

async function probeStorage(): Promise<{
  storageQuotaBytes: number | null;
  storageUsageBytes: number | null;
}> {
  const estimate = globalThis.navigator?.storage?.estimate;
  if (typeof estimate !== "function") {
    return { storageQuotaBytes: null, storageUsageBytes: null };
  }
  try {
    const result = await estimate.call(globalThis.navigator.storage);
    return {
      storageQuotaBytes: typeof result.quota === "number" ? result.quota : null,
      storageUsageBytes: typeof result.usage === "number" ? result.usage : null,
    };
  } catch {
    return { storageQuotaBytes: null, storageUsageBytes: null };
  }
}

/**
 * Read everything the browser will tell us. Never throws: a probe that fails is
 * itself the answer (that capability is absent), and Private Mode must still open.
 */
export async function collectEnvironment(
  nav?: Navigator,
  win?: Window,
): Promise<EnvironmentReport> {
  const navigator = nav ?? globalThis.navigator;
  const window = win ?? globalThis.window;
  const wasm = probeWasm();
  const gpu = await probeWebgpu(navigator);
  const storage = await probeStorage();
  const userAgent = navigator?.userAgent ?? "";
  const memory = (navigator as { deviceMemory?: number } | undefined)?.deviceMemory;

  return {
    secureContext: window?.isSecureContext ?? false,
    crossOriginIsolated: window?.crossOriginIsolated ?? false,
    ...gpu,
    ...wasm,
    hardwareConcurrency: navigator?.hardwareConcurrency ?? null,
    deviceMemoryGb: typeof memory === "number" ? memory : null,
    ...storage,
    indexedDb: has(navigator) && (has(window?.indexedDB) || has(feature("indexedDB"))),
    cacheApi: has(window?.caches) || has(feature("caches")),
    webCrypto: has(globalThis.crypto?.subtle),
    serviceWorker: has(window?.navigator?.serviceWorker) || has(feature("serviceWorker")),
    isMobile: /android|iphone|ipad|mobile/i.test(userAgent),
    platform: navigator?.platform || userAgent.slice(0, 40) || "unknown",
  };
}

/**
 * Decide what this device can run, and say why in plain language.
 *
 * The order of the checks matters for the explanation: the first blocking reason
 * is the one the user reads, so the most fundamental (no secure context, no
 * storage) comes before the most common (no WebGPU).
 */
export function assess(
  report: EnvironmentReport,
  policy: CapabilityPolicy = DEFAULT_CAPABILITY_POLICY,
): CapabilityAssessment {
  const blocking: CapabilityCode[] = [];
  const advisories: CapabilityCode[] = [];

  if (!report.secureContext) {
    blocking.push("secure-context");
  }
  if (!report.webCrypto) {
    // Without WebCrypto there is no honest way to store a transcript locally, so
    // Private Mode stores nothing rather than storing it in the clear.
    blocking.push("webcrypto");
  }
  if (!report.indexedDb) {
    blocking.push("indexeddb");
  }
  if (!report.cacheApi) {
    advisories.push("cache-api");
  }
  if (!report.serviceWorker) {
    advisories.push("service-worker");
  }

  const cores = report.hardwareConcurrency;
  const memory = report.deviceMemoryGb;
  const freeBytes =
    report.storageQuotaBytes === null || report.storageUsageBytes === null
      ? null
      : report.storageQuotaBytes - report.storageUsageBytes;

  if (!report.wasm) {
    blocking.push("wasm");
  } else {
    if (!report.wasmSimd) {
      if (policy.requireSimdForClassifiers) {
        blocking.push("wasm-simd");
      } else {
        advisories.push("wasm-simd");
      }
    }
    if (!report.wasmThreads || !report.crossOriginIsolated) {
      // Threads need SharedArrayBuffer, which needs cross-origin isolation. Without
      // them ONNX still runs, single-threaded and slower.
      advisories.push("wasm-threads");
    }
  }

  if (cores !== null && cores < policy.minCoresForClassifiers) {
    blocking.push("cores");
  }
  if (freeBytes !== null && freeBytes < policy.minFreeBytes) {
    blocking.push("storage");
  }

  const classifiers =
    !blocking.includes("wasm") &&
    !blocking.includes("wasm-simd") &&
    !blocking.includes("cores") &&
    !blocking.includes("storage") &&
    !blocking.includes("indexeddb");

  // The reply model needs WebGPU *and* the headroom to hold a 1-2 GB model.
  const llmHeadroom =
    (cores === null || cores >= policy.minCoresForLlm) &&
    (memory === null || memory >= policy.minMemoryGbForLlm);
  if (!report.webgpu) {
    blocking.push("webgpu");
  } else if (!llmHeadroom) {
    advisories.push(cores !== null && cores < policy.minCoresForLlm ? "cores" : "memory");
  }
  if (memory !== null && memory < policy.minMemoryGbForLlm && report.webgpu) {
    // Already covered above when headroom failed; kept out of `blocking` because a
    // device that reports 2 GB may still run a 1 GB quantised model slowly.
    advisories.push("memory");
  }
  const replies = report.webgpu && llmHeadroom && classifiers;

  const tier: CapabilityTier = replies ? "full" : classifiers ? "classifiers" : "rules-only";
  const encryptedStorage = report.webCrypto && report.indexedDb && report.secureContext;
  const usable = encryptedStorage;

  return {
    tier,
    supported: { classifiers, replies, encryptedStorage },
    blocking: unique(blocking),
    advisories: unique(advisories.filter((code) => !blocking.includes(code))),
    explanation: explain(tier, unique(blocking), report),
    offerStandardMode: tier === "rules-only" || !replies,
    usable,
  };
}

function unique(codes: CapabilityCode[]): CapabilityCode[] {
  return [...new Set(codes)];
}

/**
 * One honest paragraph. It names what is missing, what that costs, and what is
 * still available — and it does not pretend the on-device model is as good as the
 * server's, because it is not (that label lives in the UI next to the reply).
 */
function explain(
  tier: CapabilityTier,
  blocking: CapabilityCode[],
  report: EnvironmentReport,
): string {
  if (!report.secureContext) {
    return (
      "This page is not running in a secure context, so the browser will not give it the " +
      "encryption Private Mode needs. Nothing can be stored safely here, so Private Mode " +
      "cannot start. Standard mode is available instead."
    );
  }
  if (!report.webCrypto) {
    return (
      "This browser has not given the page WebCrypto, so there is no way to encrypt a " +
      "transcript before storing it. Private Mode would rather store nothing than store " +
      "your words in the clear, so it cannot start here. Standard mode is available instead."
    );
  }
  if (!report.indexedDb) {
    return (
      "This browser has no local storage available (private browsing can switch it off). " +
      "Private Mode keeps its transcript and models on the device, so it cannot start."
    );
  }
  if (tier === "full") {
    const adapter = report.webgpuAdapterName ? ` (${report.webgpuAdapterName})` : "";
    return `This device can run Private Mode in full: WebGPU${adapter} for the small on-device model, and WebAssembly for the classifiers.`;
  }
  if (tier === "classifiers") {
    const why = blocking.includes("webgpu")
      ? "This device has no usable WebGPU, which the small on-device reply model needs."
      : "This device does not have the memory or cores the on-device reply model needs.";
    return `${why} The emotion and safety classifiers can still run here on WebAssembly, and the safety rules always run. Replies will be shorter and more templated than standard mode — that is the honest trade.`;
  }
  const reasons = blocking
    .map((code) => REASON_SHORT[code])
    .filter((text): text is string => typeof text === "string");
  return `This device cannot run the on-device models${
    reasons.length > 0 ? `: ${reasons.join("; ")}` : ""
  }. The safety rules and the crisis support still work entirely on the device, so Private Mode can stay useful — but replies come from templates, not a model. Standard mode will give fuller replies, at the cost of your message text going to the server.`;
}

/** Short noun phrases used when several things are missing at once. */
const REASON_SHORT: Partial<Record<CapabilityCode, string>> = {
  wasm: "no WebAssembly",
  "wasm-simd": "no WebAssembly SIMD",
  cores: "too few processor cores",
  memory: "not enough memory",
  storage: "not enough free storage",
  webgpu: "no WebGPU",
  "cache-api": "no Cache API",
};

/** Format bytes for the panel: sizes here are gigabytes, never kilobytes. */
export function formatBytes(bytes: number | null): string {
  if (bytes === null || !Number.isFinite(bytes)) {
    return "unknown";
  }
  if (bytes >= GB) {
    return `${(bytes / GB).toFixed(1)} GB`;
  }
  const mb = bytes / (1024 * 1024);
  return `${mb.toFixed(mb >= 100 ? 0 : 1)} MB`;
}

/** A stable snapshot of the report, for the diagnostics line and for tests. */
export function summarise(report: EnvironmentReport): string {
  return [
    `webgpu=${report.webgpu ? "yes" : "no"}`,
    `wasm=${report.wasm ? (report.wasmSimd ? "simd" : "yes") : "no"}`,
    `threads=${report.wasmThreads && report.crossOriginIsolated ? "yes" : "no"}`,
    `cores=${report.hardwareConcurrency ?? "?"}`,
    `memory=${report.deviceMemoryGb === null ? "?" : `${report.deviceMemoryGb}GB`}`,
    `free=${formatBytes(
      report.storageQuotaBytes === null || report.storageUsageBytes === null
        ? null
        : report.storageQuotaBytes - report.storageUsageBytes,
    )}`,
    `idb=${report.indexedDb ? "yes" : "no"}`,
  ].join(" ");
}
