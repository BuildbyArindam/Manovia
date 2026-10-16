/**
 * The download manager, exercised the way a bad connection would.
 *
 * These are the checks the Private Mode promise rests on: nothing downloads without
 * consent, the size and progress shown are the real numbers, an interrupted download
 * resumes instead of starting over, a file that does not match its hash is deleted
 * rather than loaded, and "delete downloaded models" really deletes them.
 *
 * The network is a `ScriptedFetcher` and the disk an `InMemorySink`, so a transfer
 * can be cut mid-chunk, stalled, or made to serve corrupted bytes on demand. The
 * hasher is real WebCrypto, not a stub: a hash check that was faked could not fail.
 */

import { beforeEach, describe, expect, it } from "vitest";

import { DownloadManager, InMemoryKeyValueStore, InMemorySink, fileKey } from "./downloadManager";
import type { ModelManifest } from "./manifest";
import { installBlocker, modelById } from "./manifest";
import type { ModelEntry } from "./manifest";
import type { ModelId } from "./types";
import type { ProgressUpdate } from "./types";
import {
  FakeRuntimeInstaller,
  ScriptedFetcher,
  Sha256Hasher,
  fixtureBytes,
  fixtureManifest,
} from "../testSupport/modelFakes";
import { readModelManifest } from "../testSupport/repoArtifacts";

const hasher = new Sha256Hasher();

/** Two files: a small config and a 64 KB "weights" blob, chunked at 8 KB. */
const CONFIG = fixtureBytes(512, 7);
const WEIGHTS = fixtureBytes(64 * 1024, 11);
const CHUNK = 8 * 1024;

interface Harness {
  manifest: ModelManifest;
  sink: InMemorySink;
  store: InMemoryKeyValueStore;
  fetcher: ScriptedFetcher;
  installer: FakeRuntimeInstaller;
  manager: DownloadManager;
  updates: ProgressUpdate[];
  listen: (update: ProgressUpdate) => void;
}

async function harness(
  resources: ConstructorParameters<typeof ScriptedFetcher>[0] = {},
  options: {
    unpinned?: ("emotion" | "safety" | "reply")[];
    runtimeManaged?: ("emotion" | "safety" | "reply")[];
    planned?: ("emotion" | "safety" | "reply")[];
    freeBytes?: number | null;
    clock?: () => number;
  } = {},
): Promise<Harness> {
  const manifest = await fixtureManifest(
    [
      { modelId: "emotion", path: "config.json", bytes: CONFIG },
      { modelId: "emotion", path: "model.onnx", bytes: WEIGHTS },
    ],
    {
      unpinned: options.unpinned,
      runtimeManaged: options.runtimeManaged ?? ["reply"],
      planned: options.planned ?? ["safety"],
    },
  );
  const sink = new InMemorySink();
  const store = new InMemoryKeyValueStore();
  const fetcher = new ScriptedFetcher({
    "https://models.invalid/emotion/config.json": { bytes: CONFIG, chunkSize: CHUNK },
    "https://models.invalid/emotion/model.onnx": { bytes: WEIGHTS, chunkSize: CHUNK },
    ...resources,
  });
  const installer = new FakeRuntimeInstaller();
  let tick = 0;
  const manager = new DownloadManager({
    manifest,
    sink,
    fetcher,
    hasher,
    store,
    installer,
    now: options.clock ?? (() => (tick += 1000)),
    isoNow: () => "2026-10-16T00:00:00.000Z",
    freeBytes: async () => (options.freeBytes === undefined ? null : options.freeBytes),
    minFreeBytes: 1024,
  });
  const updates: ProgressUpdate[] = [];
  return {
    manifest,
    sink,
    store,
    fetcher,
    installer,
    manager,
    updates,
    listen: (update) => updates.push(update),
  };
}

/** The real, committed manifest: what Private Mode offers today, unpinned. */
const shipped = readModelManifest();

function entryOf(manifest: ModelManifest, id: ModelId): ModelEntry {
  const entry = modelById(manifest, id);
  if (entry === undefined) {
    throw new Error(`the manifest has no ${id} entry`);
  }
  return entry;
}

describe("consent", () => {
  let rig: Harness;
  beforeEach(async () => {
    rig = await harness();
  });

  it("refuses to download anything nobody agreed to", async () => {
    const state = await rig.manager.download("emotion", rig.listen);
    expect(state.phase).toBe("failed");
    expect(state.error?.code).toBe("consent-required");
    expect(rig.fetcher.requests).toEqual([]);
    expect(await rig.sink.keys()).toEqual([]);
  });

  it("records what the user was shown, and when", async () => {
    const prompt = await rig.manager.consentPromptFor("emotion");
    expect(prompt.declaredBytes).toBe(CONFIG.byteLength + WEIGHTS.byteLength);
    expect(prompt.blocker).toBeNull();

    const record = await rig.manager.consent("emotion");
    expect(record.grantedAt).toBe("2026-10-16T00:00:00.000Z");
    expect(record.declaredBytes).toBe(prompt.declaredBytes);
    const state = await rig.manager.stateOf("emotion");
    expect(state.consent).toEqual(record);
  });

  it("asks again when the artifact changes under an existing consent", async () => {
    await rig.manager.consent("emotion");
    const entry = modelById(rig.manifest, "emotion");
    expect(entry).toBeDefined();
    await rig.store.put("consent:emotion", {
      modelId: "emotion",
      grantedAt: "2026-01-01T00:00:00.000Z",
      declaredBytes: 1,
      upstream: "https://models.invalid/a-different-artifact",
    });
    const state = await rig.manager.download("emotion");
    expect(state.error?.code).toBe("consent-required");
    expect(state.error?.detail).toMatch(/changed since/);
    expect(rig.fetcher.requests).toEqual([]);
    expect(entry?.id).toBe("emotion");
  });

  it("deletes the bytes when consent is withdrawn", async () => {
    await rig.manager.consent("emotion");
    await rig.manager.download("emotion");
    expect(await rig.sink.usageBytes()).toBeGreaterThan(0);
    const result = await rig.manager.revokeConsent("emotion");
    expect(result.freedBytes).toBe(CONFIG.byteLength + WEIGHTS.byteLength);
    expect(await rig.sink.usageBytes()).toBe(0);
    expect((await rig.manager.stateOf("emotion")).consent).toBeNull();
  });
});

describe("progress and size", () => {
  it("reports real numbers: received, expected, rate and the file in flight", async () => {
    const rig = await harness();
    await rig.manager.consent("emotion");
    const state = await rig.manager.download("emotion", rig.listen);

    expect(state.phase).toBe("ready");
    expect(state.totalBytes).toBe(CONFIG.byteLength + WEIGHTS.byteLength);
    expect(state.receivedBytes).toBe(CONFIG.byteLength + WEIGHTS.byteLength);
    expect(state.installedAt).toBe("2026-10-16T00:00:00.000Z");
    expect(state.files.map((file) => [file.path, file.bytes, file.verified])).toEqual([
      ["config.json", CONFIG.byteLength, true],
      ["model.onnx", WEIGHTS.byteLength, true],
    ]);

    const downloading = rig.updates.filter((update) => update.phase === "downloading");
    expect(downloading.length).toBeGreaterThan(8);
    expect(downloading[0]?.file).toBe("config.json");
    expect(downloading.at(-1)?.file).toBe("model.onnx");
    // Received bytes only ever grow during one uninterrupted download.
    const received = downloading.map((update) => update.receivedBytes);
    expect(received).toEqual([...received].sort((left, right) => left - right));
    expect(received.at(-1)).toBe(CONFIG.byteLength + WEIGHTS.byteLength);
    // A rate appears once there are two samples to compare.
    expect(downloading.some((update) => (update.bytesPerSecond ?? 0) > 0)).toBe(true);
    expect(rig.updates.some((update) => update.phase === "verifying")).toBe(true);
    expect(rig.updates.some((update) => update.phase === "ready")).toBe(true);
  });

  it("refuses to start when free space cannot hold the model", async () => {
    const rig = await harness({}, { freeBytes: 2048 });
    await rig.manager.consent("emotion");
    const state = await rig.manager.download("emotion");
    expect(state.phase).toBe("failed");
    expect(state.error?.code).toBe("storage");
    expect(state.error?.detail).toMatch(/not enough free space/);
    expect(rig.fetcher.requests).toEqual([]);
  });

  it("reports an HTTP failure instead of writing a partial file as if it were fine", async () => {
    const rig = await harness({
      "https://models.invalid/emotion/model.onnx": { bytes: WEIGHTS, status: 503 },
    });
    await rig.manager.consent("emotion");
    const state = await rig.manager.download("emotion");
    expect(state.phase).toBe("failed");
    expect(state.error?.code).toBe("network");
    expect(state.error?.detail).toMatch(/503/);
  });
});

describe("resuming", () => {
  it("continues from the stored offset after the network drops mid-transfer", async () => {
    const rig = await harness({
      "https://models.invalid/emotion/model.onnx": {
        bytes: WEIGHTS,
        chunkSize: CHUNK,
        failAfterBytes: 3 * CHUNK,
      },
    });
    await rig.manager.consent("emotion");

    const first = await rig.manager.download("emotion", rig.listen);
    expect(first.phase).toBe("failed");
    expect(first.error?.code).toBe("network");
    // The bytes that did arrive are on the device, and the config file is complete.
    expect(await rig.sink.size(fileKey("emotion", "config.json"))).toBe(CONFIG.byteLength);
    expect(await rig.sink.size(fileKey("emotion", "model.onnx"))).toBe(3 * CHUNK);

    // The connection comes back.
    rig.fetcher.set("https://models.invalid/emotion/model.onnx", {
      bytes: WEIGHTS,
      chunkSize: CHUNK,
    });
    const second = await rig.manager.download("emotion", rig.listen);
    expect(second.phase).toBe("ready");
    expect(second.error).toBeNull();

    const onnxRequests = rig.fetcher.requests.filter((request) =>
      request.url.endsWith("model.onnx"),
    );
    expect(onnxRequests).toHaveLength(2);
    expect(onnxRequests[0]?.rangeStart).toBe(0);
    expect(onnxRequests[1]?.rangeStart).toBe(3 * CHUNK);
    // Only the missing part came over the wire the second time.
    expect(onnxRequests[1]?.bytesDelivered).toBe(WEIGHTS.byteLength - 3 * CHUNK);
    // And the finished file is byte-identical to the source, not merely the right size.
    expect(
      await hasher.sha256(
        (await rig.sink.read(fileKey("emotion", "model.onnx"))) ?? new Uint8Array(),
      ),
    ).toBe(await hasher.sha256(WEIGHTS));
  });

  it("keeps the bytes when paused, and finishes after resume", async () => {
    let release: () => void = () => undefined;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const rig = await harness({
      "https://models.invalid/emotion/model.onnx": { bytes: WEIGHTS, chunkSize: CHUNK, gate },
    });
    await rig.manager.consent("emotion");

    const inFlight = rig.manager.download("emotion", rig.listen);
    await rig.manager.pause("emotion");
    // The blocked fetch only returns once the gate opens; the abort is noticed then.
    release();
    const paused = await inFlight;
    expect(paused.phase).toBe("paused");
    expect(await rig.sink.size(fileKey("emotion", "model.onnx"))).toBe(0);

    const resumed = await rig.manager.download("emotion", rig.listen);
    expect(resumed.phase).toBe("ready");
  });

  it("starts a file over when the server ignores the Range header", async () => {
    const rig = await harness();
    await rig.manager.consent("emotion");
    // Pretend an earlier attempt left a partial file behind.
    await rig.sink.appendAt(fileKey("emotion", "model.onnx"), 0, WEIGHTS.subarray(0, CHUNK));
    rig.fetcher.set("https://models.invalid/emotion/model.onnx", {
      bytes: WEIGHTS,
      chunkSize: CHUNK,
      ignoreRange: true,
    });

    const state = await rig.manager.download("emotion", rig.listen);
    expect(state.phase).toBe("ready");
    const stored = await rig.sink.read(fileKey("emotion", "model.onnx"));
    expect(stored?.byteLength).toBe(WEIGHTS.byteLength);
    expect(await hasher.sha256(stored ?? new Uint8Array())).toBe(await hasher.sha256(WEIGHTS));
  });

  it("does not re-download a file that is already complete and verified", async () => {
    const rig = await harness();
    await rig.manager.consent("emotion");
    await rig.manager.download("emotion");
    const before = rig.fetcher.requests.length;
    const again = await rig.manager.download("emotion");
    expect(again.phase).toBe("ready");
    expect(rig.fetcher.requests).toHaveLength(before);
  });
});

describe("integrity", () => {
  it("deletes a file whose bytes do not match the pinned hash", async () => {
    const corrupted = fixtureBytes(WEIGHTS.byteLength, 11);
    corrupted[1234] = (corrupted[1234] ?? 0) ^ 0xff;
    const rig = await harness({
      "https://models.invalid/emotion/model.onnx": { bytes: corrupted, chunkSize: CHUNK },
    });
    await rig.manager.consent("emotion");

    const state = await rig.manager.download("emotion", rig.listen);
    expect(state.phase).toBe("failed");
    expect(state.error?.code).toBe("integrity-failed");
    expect(state.error?.detail).toMatch(/model\.onnx failed its sha256 check/);
    // The bad bytes are gone: a failed verification must not leave an artifact that
    // a later load could pick up.
    expect(await rig.sink.read(fileKey("emotion", "model.onnx"))).toBeNull();
    expect(state.files.find((file) => file.path === "model.onnx")?.verified).toBe(false);
  });

  it("refuses a size that does not match the manifest", async () => {
    const short = WEIGHTS.subarray(0, WEIGHTS.byteLength - 100);
    const rig = await harness({
      "https://models.invalid/emotion/model.onnx": { bytes: short, chunkSize: CHUNK },
    });
    await rig.manager.consent("emotion");
    const state = await rig.manager.download("emotion");
    expect(state.phase).toBe("failed");
    expect(state.error?.code).toBe("integrity-failed");
    expect(state.error?.detail).toMatch(/bytes, the manifest says/);
    expect(await rig.sink.read(fileKey("emotion", "model.onnx"))).toBeNull();
  });

  it("re-verifies what is already on the device, and notices a flipped byte", async () => {
    const rig = await harness();
    await rig.manager.consent("emotion");
    await rig.manager.download("emotion");
    expect((await rig.manager.verify("emotion")).ok).toBe(true);

    rig.sink.corrupt(fileKey("emotion", "config.json"), 3, 0x00);
    const result = await rig.manager.verify("emotion");
    expect(result.ok).toBe(false);
    expect(result.files[0]).toMatchObject({ path: "config.json", ok: false });
    expect(result.files[0]?.detail).toMatch(/sha256 does not match/);
    expect((await rig.manager.stateOf("emotion")).phase).toBe("failed");
  });

  it("never installs a model whose hash nobody has pinned", async () => {
    const rig = await harness({}, { unpinned: ["emotion"] });
    await rig.manager.consent("emotion");
    const state = await rig.manager.download("emotion");
    expect(state.phase).toBe("failed");
    expect(state.error?.code).toBe("not-installable");
    expect(state.error?.detail).toMatch(/models:pin/);
    expect(rig.fetcher.requests).toEqual([]);
  });
});

describe("the shipped manifest", () => {
  it("offers nothing installable until the artifacts are pinned", () => {
    // This is the honest state of the repository: the manifest lists real upstream
    // artifacts but no hash has been fetched, so the manager refuses every one of
    // them and Private Mode runs on the rules engine plus template replies.
    expect(shipped.pinned).toBe(false);
    for (const entry of shipped.models.filter((model) => !model.managed_by_runtime)) {
      expect(installBlocker(entry), entry.id).not.toBeNull();
    }
    expect(installBlocker(entryOf(shipped, "emotion"))).toMatch(/integrity hash missing/);
    const safety = entryOf(shipped, "safety");
    expect(safety.status).toBe("planned");
    expect(installBlocker(safety)).toMatch(/not shipped yet/);
    // The reply slot is refused for a different reason, and it is worth being
    // precise about why: which model WebLLM should load is configuration
    // (`PRIVATE_LLM_MODEL_ID`, see `.env.example`), and this repository is not
    // allowed to name a vendor model outside it. Committed with that unset, the
    // slot is `planned` with no upstream, so the manager refuses it and replies
    // come from the reviewed templates. When it *is* configured, WebLLM fetches and
    // caches its own artifacts, so there is nothing here to pin — which also means
    // its integrity is the runtime's business; see the known issue in PROGRESS.md
    // for Day 41.
    const reply = entryOf(shipped, "reply");
    expect(reply.managed_by_runtime).toBe(true);
    expect(reply.status).toBe("planned");
    expect(reply.upstream).toBeNull();
    expect(installBlocker(reply)).toMatch(/not shipped yet/);
  });

  it("carries an honest note for every model it lists", () => {
    for (const entry of shipped.models) {
      expect(entry.honest_note.length).toBeGreaterThan(40);
      expect(entry.label).not.toMatch(/full|complete|equivalent/i);
    }
    // Still smaller than the server model, whichever model gets named — and the
    // note says out loud that this build names none.
    expect(entryOf(shipped, "reply").honest_note).toMatch(/smaller/i);
    expect(entryOf(shipped, "reply").honest_note).toMatch(/does not name/i);
  });
});

describe("deleting", () => {
  it("frees the bytes and resets the state", async () => {
    const rig = await harness();
    await rig.manager.consent("emotion");
    await rig.manager.download("emotion");
    const used = await rig.manager.usageBytes();
    expect(used).toBe(CONFIG.byteLength + WEIGHTS.byteLength);

    const result = await rig.manager.deleteModel("emotion");
    expect(result.freedBytes).toBe(used);
    expect(await rig.manager.usageBytes()).toBe(0);
    expect(await rig.sink.keys()).toEqual([]);
    const state = await rig.manager.stateOf("emotion");
    expect(state.phase).toBe("not-downloaded");
    expect(state.receivedBytes).toBe(0);
    // Consent is the user's own record, not model data: it survives a delete.
    expect(state.consent).not.toBeNull();
  });

  it("deletes everything, including runtime-managed caches under the model prefix", async () => {
    const rig = await harness();
    await rig.manager.consent("emotion");
    await rig.manager.download("emotion");
    await rig.sink.appendAt("reply/webllm/params.bin", 0, fixtureBytes(2048, 3));

    const result = await rig.manager.deleteAll();
    expect(result.freedBytes).toBe(CONFIG.byteLength + WEIGHTS.byteLength + 2048);
    expect(result.models).toContain("emotion");
    expect(await rig.sink.keys()).toEqual([]);
  });
});

describe("runtime-managed models", () => {
  it("delegates to the runtime and relays its progress", async () => {
    const rig = await harness();
    await rig.manager.consent("reply");
    const state = await rig.manager.download("reply", rig.listen);
    expect(state.phase).toBe("ready");
    expect(rig.installer.installed).toEqual(["reply"]);
    expect(rig.updates.map((update) => update.phase)).toContain("loading");
    expect(rig.updates.at(-1)?.phase).toBe("ready");
    // No bytes of its own: WebLLM caches through the Cache API itself.
    expect(await rig.sink.keys()).toEqual([]);
  });

  it("reports a runtime failure as a failure", async () => {
    const rig = await harness();
    rig.installer.failWith = new Error("WebGPU device lost");
    await rig.manager.consent("reply");
    const state = await rig.manager.download("reply");
    expect(state.phase).toBe("failed");
    expect(state.error?.code).toBe("runtime");
    expect(state.error?.detail).toMatch(/WebGPU device lost/);
  });

  it("says so when no installer was wired up", async () => {
    const manifest = await fixtureManifest([], { runtimeManaged: ["reply"] });
    const manager = new DownloadManager({
      manifest,
      sink: new InMemorySink(),
      fetcher: new ScriptedFetcher(),
      hasher,
      store: new InMemoryKeyValueStore(),
    });
    await manager.consent("reply");
    const state = await manager.download("reply");
    expect(state.error?.code).toBe("runtime");
    expect(state.error?.detail).toMatch(/no installer was wired up/);
  });

  it("refuses a model that is planned but not shipped", async () => {
    const rig = await harness({}, { planned: ["safety"] });
    await rig.manager.consent("safety");
    const state = await rig.manager.download("safety");
    expect(state.phase).toBe("failed");
    expect(state.error?.code).toBe("not-installable");
    expect(state.error?.detail).toMatch(/not shipped yet/);
  });
});
