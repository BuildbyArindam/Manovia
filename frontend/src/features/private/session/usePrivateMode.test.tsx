/**
 * `usePrivateMode`: the boot, and the controls the panel drives.
 *
 * The bundle and the manifest are served by a stub `fetch` from the committed
 * files on disk, so this test exercises the real parse, the real capability probe
 * (in jsdom) and the real IndexedDB stores — only the network is fake, and the
 * fake records every URL it is asked for. That record is the evidence for the
 * claim this feature exists to make: booting and using Private Mode touches two
 * same-origin static files and nothing else.
 */

import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { usePrivateMode } from "./usePrivateMode";
import { BUNDLE_URL, MANIFEST_URL } from "../bundle/load";
import { closePrivateDatabase } from "../store/idb";
import { BUNDLE_PATH, MODEL_MANIFEST_PATH, readText } from "../testSupport/repoArtifacts";

const PASSPHRASE = "the monsoon reached kolkata early this year";

interface RequestRecord {
  url: string;
  init: RequestInit | undefined;
}

let requests: RequestRecord[] = [];
let failFetch = false;

/** Serves the two committed static assets; records everything it is asked for. */
const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = typeof input === "string" ? input : input.toString();
  requests.push({ url, init });
  if (failFetch) {
    throw new Error("offline");
  }
  const body = url === BUNDLE_URL ? readText(BUNDLE_PATH) : readText(MODEL_MANIFEST_PATH);
  return new Response(body, {
    status: 200,
    headers: { "content-type": "application/json" },
  });
});

async function boot() {
  const hook = renderHook(() =>
    usePrivateMode({ fetchImpl: fetchImpl as unknown as typeof fetch }),
  );
  await waitFor(() => expect(hook.result.current.booting).toBe(false), { timeout: 5000 });
  return hook;
}

/**
 * fake-indexeddb keeps the database for the whole file, so each test starts by
 * deleting it: `usageBytes` and `hasKey` are exactly the kind of assertion a leak
 * from the previous test would silently falsify.
 */
async function deleteDatabase(): Promise<void> {
  await closePrivateDatabase();
  await new Promise<void>((resolve, reject) => {
    const request = indexedDB.deleteDatabase("manovia-private");
    request.onsuccess = () => resolve();
    request.onerror = () => reject(request.error);
    request.onblocked = () => resolve();
  });
}

beforeEach(async () => {
  await deleteDatabase();
  requests = [];
  failFetch = false;
  fetchImpl.mockClear();
});

afterEach(async () => {
  await closePrivateDatabase();
});

describe("boot", () => {
  it("reads the bundle and the manifest from this origin, and nothing else", async () => {
    const { result } = await boot();

    expect(result.current.error).toBeNull();
    expect(requests.map((request) => request.url).sort()).toEqual(
      [BUNDLE_URL, MANIFEST_URL].sort(),
    );
    // No request body, and credentials explicitly omitted: two static GETs that
    // cannot carry a session cookie.
    for (const request of requests) {
      expect(request.init?.body).toBeUndefined();
      expect(request.init?.credentials).toBe("omit");
    }
    expect(result.current.bundle?.format).toBe("manovia-private-bundle/1");
    expect(result.current.manifest?.format).toBe("manovia-model-manifest/1");
    expect(result.current.bundleSha256).toMatch(/^[0-9a-f]{16,64}$/);
    expect(result.current.bundleBytes).toBeGreaterThan(100_000);
  });

  it("reports one model state per manifest entry, all undownloaded at first", async () => {
    const { result } = await boot();
    const manifest = result.current.manifest;
    expect(manifest).not.toBeNull();
    expect(result.current.models).toHaveLength(manifest?.models.length ?? 0);
    for (const state of result.current.models) {
      expect(state.phase).toBe("not-downloaded");
      expect(state.consent).toBeNull();
    }
    expect(result.current.usageBytes).toBe(0);
    expect(result.current.progress).toEqual({});
  });

  it("gives an honest verdict on a browser with no secure context", async () => {
    const { result } = await boot();
    const capability = result.current.capability;
    expect(capability).not.toBeNull();
    // jsdom reports `isSecureContext === false`, which really does block
    // WebCrypto-backed storage: the verdict must say so rather than guess.
    expect(result.current.environment?.secureContext).toBe(false);
    expect(capability?.blocking).toContain("secure-context");
    expect(capability?.usable).toBe(false);
    expect(capability?.offerStandardMode).toBe(true);
    expect(capability?.explanation.length).toBeGreaterThan(20);
  });

  it("leaves Private Mode off, and the app usable, when the bundle cannot be fetched", async () => {
    failFetch = true;
    const { result } = await boot();

    expect(result.current.error).not.toBeNull();
    expect(result.current.enabled).toBe(false);
    expect(result.current.session).toBeNull();
    expect(result.current.bundle).toBeNull();
  });
});

describe("the toggle", () => {
  it("records consent that the capability check was shown, and builds a session", async () => {
    const { result } = await boot();
    expect(result.current.enabled).toBe(false);
    expect(result.current.session).toBeNull();

    await act(async () => {
      await result.current.setEnabled(true);
    });

    expect(result.current.enabled).toBe(true);
    expect(result.current.settings?.privateMode).toBe(true);
    expect(result.current.settings?.consent.capabilityCheckedAt).not.toBeNull();
    // Rules-only is still a real session: the engine, escalator and guard need no
    // model, so Private Mode answers even where nothing can be downloaded.
    expect(result.current.session).not.toBeNull();
  });

  it("drops the session when it is switched off again", async () => {
    const { result } = await boot();
    await act(async () => {
      await result.current.setEnabled(true);
    });
    await act(async () => {
      await result.current.setEnabled(false);
    });
    expect(result.current.enabled).toBe(false);
    expect(result.current.session).toBeNull();
    expect(result.current.locked).toBe(true);
  });

  it("still fetches nothing but the two static assets after a full on/off cycle", async () => {
    const { result } = await boot();
    await act(async () => {
      await result.current.setEnabled(true);
    });
    await act(async () => {
      await result.current.setLocale("hi");
    });
    await act(async () => {
      await result.current.setRegion("IN");
    });
    await act(async () => {
      await result.current.setEnabled(false);
    });

    expect(new Set(requests.map((request) => request.url))).toEqual(
      new Set([BUNDLE_URL, MANIFEST_URL]),
    );
    expect(result.current.settings?.locale).toBe("hi");
    expect(result.current.settings?.region).toBe("IN");
  });
});

describe("the passphrase", () => {
  it("creates a key, locks it, refuses the wrong passphrase and accepts the right one", async () => {
    const { result } = await boot();
    await act(async () => {
      await result.current.setEnabled(true);
    });
    expect(result.current.hasKey).toBe(false);

    let created: { ok: boolean; advice: string } = { ok: false, advice: "" };
    await act(async () => {
      created = await result.current.createKey(PASSPHRASE);
    });
    expect(created.ok).toBe(true);
    expect(result.current.hasKey).toBe(true);
    expect(result.current.locked).toBe(false);

    act(() => {
      result.current.lock();
    });
    expect(result.current.locked).toBe(true);

    let wrong: { ok: boolean; reason?: string } = { ok: true };
    await act(async () => {
      wrong = await result.current.unlock("a completely different sentence here");
    });
    expect(wrong).toEqual({ ok: false, reason: "wrong-passphrase" });
    expect(result.current.locked).toBe(true);

    let right: { ok: boolean; reason?: string } = { ok: false };
    await act(async () => {
      right = await result.current.unlock(PASSPHRASE);
    });
    expect(right.ok).toBe(true);
    expect(result.current.locked).toBe(false);
  }, 20_000);

  it("rejects a weak passphrase with advice instead of creating a key", async () => {
    const { result } = await boot();
    let outcome: { ok: boolean; advice: string } = { ok: true, advice: "" };
    await act(async () => {
      outcome = await result.current.createKey("abc");
    });
    expect(outcome.ok).toBe(false);
    expect(outcome.advice.length).toBeGreaterThan(0);
    expect(result.current.hasKey).toBe(false);
  });

  it("wipes the key and everything stored with it", async () => {
    const { result } = await boot();
    await act(async () => {
      await result.current.setEnabled(true);
    });
    await act(async () => {
      await result.current.createKey(PASSPHRASE);
    });
    expect(result.current.hasKey).toBe(true);

    let messages = -1;
    await act(async () => {
      messages = await result.current.wipe();
    });
    expect(messages).toBe(0);
    expect(result.current.hasKey).toBe(false);
    expect(result.current.locked).toBe(true);
  }, 20_000);
});

describe("the model manager", () => {
  it("has nothing to free before anything was downloaded", async () => {
    const { result } = await boot();
    let freed = -1;
    await act(async () => {
      freed = await result.current.deleteAllModels();
    });
    expect(freed).toBe(0);
    expect(result.current.usageBytes).toBe(0);
  });

  it("refuses to download a model whose integrity hash is not pinned", async () => {
    const { result } = await boot();
    // The committed manifest is unpinned: `installBlocker` says so, and the
    // manager must fail closed rather than fetch unverified bytes.
    await act(async () => {
      await result.current.consentAndDownload("emotion");
    });
    const state = result.current.models.find((model) => model.modelId === "emotion");
    expect(state?.phase).toBe("failed");
    expect(state?.error?.code).toBe("not-installable");
  });
});
