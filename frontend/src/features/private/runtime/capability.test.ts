/**
 * Capability detection.
 *
 * The decision table is the product here: a device either runs the models, runs only
 * the classifiers, or runs the rules alone — and in every case the user is told why
 * in one honest sentence. Those sentences are asserted, because "not supported" is
 * the failure mode this exists to avoid.
 *
 * Fixtures stand in for hardware this sandbox does not have. `collectEnvironment()`
 * is also called for real (in jsdom) to prove the probe itself never throws: a
 * capability check that crashes would take Private Mode down with it.
 */

import { describe, expect, it } from "vitest";

import {
  DEFAULT_CAPABILITY_POLICY,
  assess,
  collectEnvironment,
  formatBytes,
  probeWebgpu,
  summarise,
} from "./capability";
import type { EnvironmentReport } from "./capability";

/** A capable laptop: WebGPU, SIMD, threads, 8 cores, 8 GB, room to spare. */
function strong(overrides: Partial<EnvironmentReport> = {}): EnvironmentReport {
  return {
    secureContext: true,
    crossOriginIsolated: true,
    webgpu: true,
    webgpuAdapterName: "Intel(R) Iris(R) Xe",
    webgpuError: null,
    wasm: true,
    wasmSimd: true,
    wasmThreads: true,
    hardwareConcurrency: 8,
    deviceMemoryGb: 8,
    storageQuotaBytes: 120 * 1024 * 1024 * 1024,
    storageUsageBytes: 10 * 1024 * 1024 * 1024,
    indexedDb: true,
    cacheApi: true,
    webCrypto: true,
    serviceWorker: true,
    isMobile: false,
    platform: "Linux x86_64",
    ...overrides,
  };
}

describe("the tier decision", () => {
  it("runs everything on a capable device, and says which GPU it found", () => {
    const result = assess(strong());
    expect(result.tier).toBe("full");
    expect(result.supported).toEqual({
      classifiers: true,
      replies: true,
      encryptedStorage: true,
    });
    expect(result.blocking).toEqual([]);
    expect(result.usable).toBe(true);
    expect(result.explanation).toMatch(/WebGPU/);
    expect(result.explanation).toContain("Intel(R) Iris(R) Xe");
  });

  it("drops to the classifiers when there is no WebGPU, and explains the trade", () => {
    const result = assess(
      strong({ webgpu: false, webgpuAdapterName: null, webgpuError: "no gpu" }),
    );
    expect(result.tier).toBe("classifiers");
    expect(result.supported.replies).toBe(false);
    expect(result.supported.classifiers).toBe(true);
    expect(result.blocking).toContain("webgpu");
    expect(result.offerStandardMode).toBe(true);
    expect(result.explanation).toMatch(/no usable WebGPU/);
    expect(result.explanation).toMatch(/shorter and more templated/);
  });

  it("drops to rules only when WebAssembly is missing", () => {
    const result = assess(
      strong({ webgpu: false, wasm: false, wasmSimd: false, wasmThreads: false }),
    );
    expect(result.tier).toBe("rules-only");
    expect(result.supported.classifiers).toBe(false);
    expect(result.blocking).toContain("wasm");
    expect(result.explanation).toMatch(/cannot run the on-device models/);
    expect(result.explanation).toMatch(/safety rules .*still work entirely on the device/);
    expect(result.explanation).toMatch(/at the cost of your message text going to the server/);
  });

  it("treats missing SIMD as blocking by default, because slow is not usable", () => {
    const strict = assess(strong({ wasmSimd: false }));
    expect(strict.blocking).toContain("wasm-simd");
    expect(strict.supported.classifiers).toBe(false);

    const lenient = assess(strong({ wasmSimd: false }), {
      ...DEFAULT_CAPABILITY_POLICY,
      requireSimdForClassifiers: false,
    });
    expect(lenient.advisories).toContain("wasm-simd");
    expect(lenient.supported.classifiers).toBe(true);
  });

  it("keeps the classifiers but not the model on a low-memory WebGPU device", () => {
    const result = assess(strong({ deviceMemoryGb: 2 }));
    expect(result.tier).toBe("classifiers");
    expect(result.supported.replies).toBe(false);
    expect(result.advisories).toContain("memory");
    expect(result.explanation).toMatch(/memory or cores/);
  });

  it("refuses the model on a two-core phone even with WebGPU", () => {
    const result = assess(strong({ hardwareConcurrency: 2, deviceMemoryGb: 4, isMobile: true }));
    expect(result.supported.replies).toBe(false);
    // Two cores still runs the classifiers: the floor is the policy's own number.
    expect(result.supported.classifiers).toBe(true);
    expect(result.tier).toBe("classifiers");

    const oneCore = assess(strong({ hardwareConcurrency: 1 }));
    expect(oneCore.blocking).toContain("cores");
    expect(oneCore.supported.classifiers).toBe(false);
    expect(oneCore.tier).toBe("rules-only");
  });

  it("refuses to download into a nearly full disk", () => {
    const result = assess(
      strong({
        storageQuotaBytes: 4 * 1024 * 1024 * 1024,
        storageUsageBytes: 3 * 1024 * 1024 * 1024,
      }),
    );
    expect(result.blocking).toContain("storage");
    expect(result.supported.classifiers).toBe(false);
    expect(result.explanation).toMatch(/not enough free storage/);
  });

  it("does not guess when the browser will not report memory or storage", () => {
    // `navigator.deviceMemory` is Chrome-only and `storage.estimate()` can be
    // unavailable. Absent data must not read as "too small".
    const result = assess(
      strong({ deviceMemoryGb: null, storageQuotaBytes: null, storageUsageBytes: null }),
    );
    expect(result.tier).toBe("full");
    expect(result.blocking).toEqual([]);
    expect(
      summarise(strong({ deviceMemoryGb: null, storageQuotaBytes: null, storageUsageBytes: null })),
    ).toContain("memory=?");
  });

  it("will not store anything without WebCrypto or IndexedDB, and says so", () => {
    const noCrypto = assess(strong({ webCrypto: false }));
    expect(noCrypto.usable).toBe(false);
    expect(noCrypto.supported.encryptedStorage).toBe(false);
    expect(noCrypto.blocking).toContain("webcrypto");
    expect(noCrypto.explanation).toMatch(/rather store nothing than store your words in the clear/);

    const noIdb = assess(strong({ indexedDb: false }));
    expect(noIdb.usable).toBe(false);
    expect(noIdb.blocking).toContain("indexeddb");
    expect(noIdb.explanation).toMatch(/private browsing/);

    const insecure = assess(strong({ secureContext: false }));
    expect(insecure.usable).toBe(false);
    expect(insecure.blocking).toContain("secure-context");
    expect(insecure.explanation).toMatch(/secure context/);
  });

  it("mentions threads as an advisory, not a blocker: slower is still usable", () => {
    const result = assess(strong({ crossOriginIsolated: false }));
    expect(result.advisories).toContain("wasm-threads");
    expect(result.blocking).not.toContain("wasm-threads");
    expect(result.supported.classifiers).toBe(true);
  });

  it("never repeats a code in both lists", () => {
    const result = assess(strong({ webgpu: false, wasmSimd: false, hardwareConcurrency: 1 }));
    for (const code of result.advisories) {
      expect(result.blocking).not.toContain(code);
    }
    expect(new Set(result.blocking).size).toBe(result.blocking.length);
  });

  it("formats sizes the way the consent screen needs them", () => {
    expect(formatBytes(1536 * 1024 * 1024)).toBe("1.5 GB");
    expect(formatBytes(64 * 1024 * 1024)).toBe("64.0 MB");
    expect(formatBytes(250 * 1024 * 1024)).toBe("250 MB");
    expect(formatBytes(null)).toBe("unknown");
  });
});

describe("probing the real environment", () => {
  it("answers without throwing in a browser that has no WebGPU", async () => {
    // jsdom: no navigator.gpu, no caches, no service worker, and — because vitest
    // serves it over http://localhost — no secure context. The probe has to report
    // all of that as data rather than fail, and the assessment has to refuse to
    // store anything on an insecure origin instead of pretending otherwise.
    const report = await collectEnvironment();
    expect(report.webgpu).toBe(false);
    expect(report.webgpuError).not.toBeNull();
    expect(typeof report.wasm).toBe("boolean");
    expect(report.indexedDb).toBe(true);
    expect(report.webCrypto).toBe(true);
    expect(report.secureContext).toBe(false);
    expect(summarise(report)).toMatch(/^webgpu=no wasm=/);

    const insecure = assess(report);
    expect(insecure.usable).toBe(false);
    expect(insecure.blocking).toContain("secure-context");
    expect(insecure.blocking).toContain("webgpu");

    // The same device over https: usable, rules-only tier, WebGPU named as the gap.
    const secure = assess({ ...report, secureContext: true });
    expect(secure.usable).toBe(true);
    expect(secure.tier).not.toBe("full");
    expect(secure.blocking).toEqual(["webgpu"]);
  });

  it("reports a missing requestAdapter as a reason, not a crash", async () => {
    const withoutGpu = await probeWebgpu({} as Navigator);
    expect(withoutGpu.webgpu).toBe(false);
    expect(withoutGpu.webgpuError).toMatch(/not available/);

    const withBrokenGpu = await probeWebgpu({
      gpu: {
        requestAdapter: () => Promise.reject(new Error("device lost")),
      },
    } as unknown as Navigator);
    expect(withBrokenGpu.webgpu).toBe(false);
    expect(withBrokenGpu.webgpuError).toBe("device lost");

    const withAdapter = await probeWebgpu({
      gpu: {
        requestAdapter: () =>
          Promise.resolve({ info: { vendor: "intel", description: "iris xe" } }),
      },
    } as unknown as Navigator);
    expect(withAdapter.webgpu).toBe(true);
    expect(withAdapter.webgpuAdapterName).toBe("intel iris xe");

    const nameless = await probeWebgpu({
      gpu: { requestAdapter: () => Promise.resolve({}) },
    } as unknown as Navigator);
    expect(nameless.webgpu).toBe(true);
    expect(nameless.webgpuAdapterName).toBe("unknown adapter");

    const nullAdapter = await probeWebgpu({
      gpu: { requestAdapter: () => Promise.resolve(null) },
    } as unknown as Navigator);
    expect(nullAdapter.webgpu).toBe(false);
    expect(nullAdapter.webgpuError).toMatch(/no adapter/);
  });

  it("detects the WASM features this runtime actually has", async () => {
    const report = await collectEnvironment();
    // Node's V8 has SIMD; whether it has threads depends on the build, so only the
    // implication is asserted: threads without SIMD would be a broken probe.
    if (report.wasmThreads) {
      expect(report.wasmSimd).toBe(true);
    }
    expect(report.wasm).toBe(true);
  });
});
