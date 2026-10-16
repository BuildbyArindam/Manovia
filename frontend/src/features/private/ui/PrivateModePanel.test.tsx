/**
 * The Private Mode panel.
 *
 * These tests drive the panel with a stub controller rather than a real boot: the
 * boot is covered in `usePrivateMode.test.ts`, and here the contract under test is
 * what the panel *says and offers* for a given device and model state.
 *
 * The assertions that matter are the honest ones — size and caveat shown before
 * consent, a blocker shown instead of a download button, progress in a single live
 * region, and confirmation before anything is deleted.
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { describe, expect, it, vi } from "vitest";

import { PrivateModePanel } from "./PrivateModePanel";
import { PRIVATE_COPY } from "./copy";
import { assess } from "../runtime/capability";
import type { EnvironmentReport } from "../runtime/capability";
import type { ModelState } from "../models/downloadManager";
import { DEFAULT_SETTINGS } from "../store/localStore";
import type { PrivateModeController } from "../session/usePrivateMode";
import { readBundle, readModelManifest } from "../testSupport/repoArtifacts";

const bundle = readBundle();
const manifest = readModelManifest();

/** A capable laptop (the same fixture shape as `capability.test.ts`). */
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

function consent(modelId: ModelState["modelId"], declaredBytes: number) {
  return {
    modelId,
    grantedAt: "2026-10-16T00:00:00.000Z",
    declaredBytes,
    upstream: null,
  };
}

function state(modelId: ModelState["modelId"], overrides: Partial<ModelState> = {}): ModelState {
  return {
    modelId,
    phase: "not-downloaded",
    receivedBytes: 0,
    totalBytes: 0,
    bytesPerSecond: null,
    consent: null,
    installedAt: null,
    error: null,
    files: [],
    ...overrides,
  };
}

function stub(overrides: Partial<PrivateModeController> = {}): PrivateModeController {
  const environment = overrides.environment ?? strong();
  return {
    booting: false,
    error: null,
    bundle,
    manifest,
    bundleSha256: "0".repeat(16),
    bundleBytes: 191_000,
    environment,
    capability: assess(environment),
    settings: DEFAULT_SETTINGS,
    models: [],
    usageBytes: 0,
    enabled: false,
    locked: true,
    hasKey: false,
    progress: {},
    session: null,
    setEnabled: vi.fn(async () => undefined),
    setRegion: vi.fn(async () => undefined),
    setLocale: vi.fn(async () => undefined),
    setStoreTranscript: vi.fn(async () => undefined),
    consentAndDownload: vi.fn(async () => undefined),
    pause: vi.fn(async () => undefined),
    cancel: vi.fn(async () => undefined),
    verify: vi.fn(async () => ({ ok: true })),
    deleteModel: vi.fn(async () => undefined),
    deleteAllModels: vi.fn(async () => 0),
    createKey: vi.fn(async () => ({ ok: true, advice: "" })),
    unlock: vi.fn(async () => ({ ok: true })),
    lock: vi.fn(() => undefined),
    wipe: vi.fn(async () => 0),
    ...overrides,
  };
}

describe("the Private Mode switch", () => {
  it("is off by default and says what off means", () => {
    render(<PrivateModePanel controller={stub()} />);
    const toggle = screen.getByTestId("private-mode-toggle");
    expect(toggle).not.toBeChecked();
    expect(toggle).toHaveAccessibleName(PRIVATE_COPY.enable);
    expect(screen.getByText(PRIVATE_COPY.disabledHint)).toBeInTheDocument();
    // Nothing to consent to, nothing to delete, until Private Mode is on.
    expect(screen.queryByTestId("model-reply")).toBeNull();
    expect(screen.queryByTestId("private-storage")).toBeNull();
  });

  it("is disabled and explains itself while the device probe runs", () => {
    render(<PrivateModePanel controller={stub({ booting: true })} />);
    expect(screen.getByTestId("private-mode-toggle")).toBeDisabled();
    expect(screen.getByTestId("private-booting")).toHaveTextContent(PRIVATE_COPY.booting);
  });

  it("calls setEnabled when it is switched on", async () => {
    const user = userEvent.setup();
    const setEnabled = vi.fn(async () => undefined);
    render(<PrivateModePanel controller={stub({ setEnabled })} />);
    await user.click(screen.getByTestId("private-mode-toggle"));
    expect(setEnabled).toHaveBeenCalledWith(true);
  });

  it("shows a boot failure as an alert, and keeps the panel readable", () => {
    render(<PrivateModePanel controller={stub({ error: "bundle fetch failed" })} />);
    const alert = screen.getByTestId("private-boot-error");
    expect(alert).toHaveAttribute("role", "alert");
    expect(alert).toHaveTextContent(PRIVATE_COPY.bootFailed);
    expect(alert).toHaveTextContent("bundle fetch failed");
  });
});

describe("the capability verdict", () => {
  it("offers standard mode, in one sentence, when the models cannot run", () => {
    // No WebGPU, no SIMD, two cores: the honest tier is "rules only".
    const environment = strong({
      webgpu: false,
      webgpuAdapterName: null,
      webgpuError: "no adapter",
      wasmSimd: false,
      wasmThreads: false,
      hardwareConcurrency: 2,
      deviceMemoryGb: 2,
    });
    render(<PrivateModePanel controller={stub({ enabled: true, environment })} />);
    expect(assess(environment).tier).toBe("rules-only");
    expect(screen.getByTestId("private-tier")).toHaveTextContent(PRIVATE_COPY.tierRules);
    expect(screen.getByTestId("private-offer-standard")).toHaveTextContent(
      PRIVATE_COPY.offerStandard,
    );
    // The probe's own English explanation stays, but collapsed and labelled.
    const details = screen.getByTestId("private-capability-details");
    expect(details).toHaveTextContent(PRIVATE_COPY.whyHeading);
  });

  it("always says the on-device model is the smaller one", () => {
    render(<PrivateModePanel controller={stub({ enabled: true })} />);
    expect(screen.getByTestId("private-smaller-model")).toHaveTextContent(
      PRIVATE_COPY.smallerModel,
    );
  });
});

describe("the model manager", () => {
  it("shows size and the honest note for every model before any consent", () => {
    render(<PrivateModePanel controller={stub({ enabled: true })} />);
    for (const entry of manifest.models) {
      const row = screen.getByTestId(`model-${entry.id}`);
      expect(row).toHaveTextContent(entry.label);
      expect(screen.getByTestId(`model-${entry.id}-honest`)).toHaveTextContent(entry.honest_note);
      expect(screen.getByTestId(`model-${entry.id}-size`)).not.toBeEmptyDOMElement();
    }
    expect(screen.getByTestId("private-models-lede")).toHaveTextContent(PRIVATE_COPY.modelsLede);
  });

  it("replaces the download button with the blocker when a model cannot be trusted yet", () => {
    render(<PrivateModePanel controller={stub({ enabled: true })} />);
    // The committed manifest is unpinned (no Hugging Face egress in CI), so the
    // classifiers must refuse to download rather than fetch unverified bytes.
    const emotion = screen.getByTestId("model-emotion");
    expect(emotion).toHaveTextContent("integrity hash missing");
    expect(screen.queryByTestId("model-emotion-download")).toBeNull();
    // The safety model is not shipped at all yet.
    expect(screen.getByTestId("model-safety")).toHaveTextContent("not shipped yet");
  });

  it("offers the download, with the size in the label, for a model that can be installed", () => {
    render(<PrivateModePanel controller={stub({ enabled: true })} />);
    const reply = manifest.models.find((model) => model.id === "reply");
    expect(reply).toBeDefined();
    // WebLLM manages its own cache, so nothing here is blocked by a missing hash.
    expect(screen.getByTestId("model-reply-download")).toBeInTheDocument();
  });

  it("shows pause and cancel while a download runs, and resume when it is paused", () => {
    render(
      <PrivateModePanel
        controller={stub({
          enabled: true,
          models: [
            state("reply", {
              phase: "downloading",
              receivedBytes: 420,
              totalBytes: 1000,
              consent: consent("reply", 1000),
            }),
          ],
        })}
      />,
    );
    expect(screen.getByTestId("model-reply")).toHaveAttribute("data-phase", "downloading");
    expect(screen.getByTestId("model-reply-phase")).toHaveTextContent("42%");
    expect(screen.getByTestId("model-reply-pause")).toBeInTheDocument();
    expect(screen.getByTestId("model-reply-cancel")).toBeInTheDocument();
  });

  it("announces progress in exactly one live region", () => {
    render(
      <PrivateModePanel
        controller={stub({ enabled: true, progress: { reply: "downloading 42%" } })}
      />,
    );
    const regions = screen.getAllByRole("status");
    expect(regions).toHaveLength(1);
    expect(regions[0]).toHaveAttribute("aria-live", "polite");
    expect(regions[0]).toHaveTextContent("reply: downloading 42%");
  });

  it("offers integrity check and delete once a model is ready", async () => {
    const user = userEvent.setup();
    const verify = vi.fn(async () => ({ ok: true }));
    const deleteModel = vi.fn(async () => undefined);
    render(
      <PrivateModePanel
        controller={stub({
          enabled: true,
          verify,
          deleteModel,
          usageBytes: 900 * 1024 * 1024,
          models: [
            state("reply", {
              phase: "ready",
              receivedBytes: 900 * 1024 * 1024,
              totalBytes: 900 * 1024 * 1024,
              installedAt: "2026-10-16T00:00:00.000Z",
              consent: consent("reply", 900 * 1024 * 1024),
              files: [{ path: "model.bin", bytes: 900 * 1024 * 1024, verified: true }],
            }),
          ],
        })}
      />,
    );
    expect(screen.getByTestId("model-reply-verified")).toHaveTextContent(
      PRIVATE_COPY.modelVerified,
    );
    await user.click(screen.getByTestId("model-reply-verify"));
    expect(verify).toHaveBeenCalledWith("reply");
    await user.click(screen.getByTestId("model-reply-delete"));
    expect(deleteModel).toHaveBeenCalledWith("reply");
  });

  it("asks before deleting every downloaded model", async () => {
    const user = userEvent.setup();
    const deleteAllModels = vi.fn(async () => 900);
    render(
      <PrivateModePanel
        controller={stub({
          enabled: true,
          deleteAllModels,
          usageBytes: 900 * 1024 * 1024,
          models: [state("reply", { phase: "ready", receivedBytes: 900, totalBytes: 900 })],
        })}
      />,
    );
    await user.click(screen.getByTestId("private-delete-all"));
    expect(deleteAllModels).not.toHaveBeenCalled();
    expect(screen.getByTestId("private-delete-all-confirm")).toHaveTextContent(
      PRIVATE_COPY.wipeConfirm,
    );
    await user.click(screen.getByTestId("private-delete-all-confirm-button"));
    expect(deleteAllModels).toHaveBeenCalledTimes(1);
  });

  it("hides delete-all when nothing is stored", () => {
    render(<PrivateModePanel controller={stub({ enabled: true, usageBytes: 0 })} />);
    expect(screen.queryByTestId("private-delete-all")).toBeNull();
  });
});

describe("encrypted local storage", () => {
  it("says the passphrase is never stored and cannot be recovered", () => {
    render(<PrivateModePanel controller={stub({ enabled: true })} />);
    expect(screen.getByTestId("private-storage")).toHaveTextContent(PRIVATE_COPY.storageLede);
  });

  it("creates a key from the passphrase the reader typed", async () => {
    const user = userEvent.setup();
    const createKey = vi.fn(async () => ({ ok: true, advice: "" }));
    render(<PrivateModePanel controller={stub({ enabled: true, createKey })} />);
    await user.type(screen.getByTestId("private-passphrase"), "the monsoon came early");
    await user.click(screen.getByTestId("private-create-key"));
    expect(createKey).toHaveBeenCalledWith("the monsoon came early");
  });

  it("shows the advice when a passphrase is too weak, and does not create a key", async () => {
    const user = userEvent.setup();
    const createKey = vi.fn(async () => ({ ok: false, advice: "use at least three words" }));
    render(<PrivateModePanel controller={stub({ enabled: true, createKey })} />);
    await user.type(screen.getByTestId("private-passphrase"), "short");
    await user.click(screen.getByTestId("private-create-key"));
    const note = screen.getByTestId("private-passphrase-note");
    expect(note).toHaveAttribute("role", "alert");
    expect(note).toHaveTextContent("use at least three words");
  });

  it("unlocks with an existing key, and reports a wrong passphrase", async () => {
    const user = userEvent.setup();
    const unlock = vi.fn(async () => ({ ok: false, reason: "wrong-passphrase" }));
    render(<PrivateModePanel controller={stub({ enabled: true, hasKey: true, unlock })} />);
    await user.type(screen.getByTestId("private-passphrase"), "a different phrase entirely");
    await user.click(screen.getByTestId("private-unlock"));
    expect(unlock).toHaveBeenCalledWith("a different phrase entirely");
    expect(screen.getByTestId("private-passphrase-note")).toHaveTextContent("wrong-passphrase");
  });

  it("locks, and asks before deleting everything", async () => {
    const user = userEvent.setup();
    const lock = vi.fn(() => undefined);
    const wipe = vi.fn(async () => 3);
    render(
      <PrivateModePanel
        controller={stub({ enabled: true, hasKey: true, locked: false, lock, wipe })}
      />,
    );
    await user.click(screen.getByTestId("private-lock"));
    expect(lock).toHaveBeenCalledTimes(1);
    await user.click(screen.getByTestId("private-wipe"));
    expect(wipe).not.toHaveBeenCalled();
    expect(screen.getByTestId("private-wipe-confirm")).toHaveTextContent(PRIVATE_COPY.wipeConfirm);
    await user.click(screen.getByTestId("private-wipe-confirm-button"));
    expect(wipe).toHaveBeenCalledTimes(1);
  });

  it("cannot turn on transcript storage before there is a key", () => {
    render(<PrivateModePanel controller={stub({ enabled: true, hasKey: false })} />);
    expect(screen.getByTestId("private-store-transcript")).toBeDisabled();
  });

  it("passes the transcript switch through to settings", async () => {
    const user = userEvent.setup();
    const setStoreTranscript = vi.fn(async () => undefined);
    render(
      <PrivateModePanel
        controller={stub({
          enabled: true,
          hasKey: true,
          locked: false,
          setStoreTranscript,
          settings: { ...DEFAULT_SETTINGS, storeTranscript: false },
        })}
      />,
    );
    await user.click(screen.getByTestId("private-store-transcript"));
    expect(setStoreTranscript).toHaveBeenCalledWith(true);
  });
});

describe("accessibility", () => {
  it("has no axe violations when Private Mode is on", async () => {
    const { container } = render(
      <PrivateModePanel
        controller={stub({
          enabled: true,
          usageBytes: 1024,
          progress: { reply: "downloading 5%" },
        })}
      />,
    );
    expect(await axe(container)).toHaveNoViolations();
  });
});
