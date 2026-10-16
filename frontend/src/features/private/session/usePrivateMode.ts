/**
 * Booting and driving Private Mode from React.
 *
 * One hook owns the whole on-device stack, because the pieces have to agree: the
 * capability verdict decides which reply runtime is offered, the download manager
 * decides whether that runtime exists, the passphrase decides whether anything is
 * stored, and the session needs all three before it can answer a message.
 *
 * Boot order (each step reported, none of them fatal):
 *
 *   1. fetch the compiled safety bundle and the model manifest (same origin)
 *   2. probe the device and decide the tier
 *   3. open IndexedDB, read settings, read model states
 *   4. build the rules engine, escalator and output guard
 *   5. choose the reply runtime: the installed model if there is one, otherwise the
 *      template writer — never the server
 *
 * A failure anywhere leaves `error` set and Private Mode off, with standard mode
 * still working: turning on a privacy feature must not be able to break the app for
 * somebody who needs it.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { ChatLocale } from "@/features/chat/types";

import { loadModelManifest, loadSafetyBundle } from "../bundle/load";
import { assess, collectEnvironment } from "../runtime/capability";
import type { CapabilityAssessment, EnvironmentReport } from "../runtime/capability";
import { buildEscalator } from "../crisis/escalation";
import { DownloadManager } from "../models/downloadManager";
import type { ModelState } from "../models/downloadManager";
import type { ModelManifest } from "../models/manifest";
import { installBlocker } from "../models/manifest";
import { TemplateReplyModel } from "../models/templateReply";
import type { ModelId, ReplyModel } from "../models/types";
import { compilePatternSet } from "../rules/bundleSchema";
import type { PrivateBundle } from "../rules/bundleSchema";
import { buildOutputGuard } from "../rules/outputGuard";
import { RuleEngine } from "../rules/ruleEngine";
import {
  IndexedDbKeyValue,
  IndexedDbSink,
  SettingsStore,
  TranscriptStore,
} from "../store/localStore";
import type { PrivateSettings } from "../store/localStore";
import { openPrivateDatabase } from "../store/idb";
import type { PrivateDatabase } from "../store/idb";
import { WebCryptoHasher, HttpFetcher, freeStorageBytes } from "../models/web/fetcher";
import { WebLlmReplyModel } from "../models/web/webllm";
import { PrivateSession } from "./privateSession";

export interface PrivateModeBoot {
  booting: boolean;
  error: string | null;
  bundle: PrivateBundle | null;
  manifest: ModelManifest | null;
  bundleSha256: string | null;
  bundleBytes: number;
  environment: EnvironmentReport | null;
  capability: CapabilityAssessment | null;
  settings: PrivateSettings | null;
  models: ModelState[];
  usageBytes: number;
}

export interface PrivateModeController extends PrivateModeBoot {
  enabled: boolean;
  locked: boolean;
  hasKey: boolean;
  setEnabled: (value: boolean) => Promise<void>;
  setRegion: (region: string | null) => Promise<void>;
  setLocale: (locale: ChatLocale) => Promise<void>;
  setStoreTranscript: (value: boolean) => Promise<void>;
  consentAndDownload: (modelId: ModelId) => Promise<void>;
  pause: (modelId: ModelId) => Promise<void>;
  cancel: (modelId: ModelId) => Promise<void>;
  verify: (modelId: ModelId) => Promise<{ ok: boolean }>;
  deleteModel: (modelId: ModelId) => Promise<void>;
  deleteAllModels: () => Promise<number>;
  createKey: (passphrase: string) => Promise<{ ok: boolean; advice: string }>;
  unlock: (passphrase: string) => Promise<{ ok: boolean; reason?: string }>;
  lock: () => void;
  wipe: () => Promise<number>;
  /** The session, or null while Private Mode is off or still booting. */
  session: PrivateSession | null;
  /** Progress text for the aria-live region while a model downloads. */
  progress: Record<string, string>;
}

export interface UsePrivateModeOptions {
  /** Injectable for tests; defaults to the browser's `fetch`. */
  fetchImpl?: typeof fetch;
  /** Called when the boot finishes, so a caller can measure it. */
  onReady?: (boot: PrivateModeBoot) => void;
}

/** One conversation per page load: Private Mode has no server-side session id. */
function conversationId(): string {
  return `private-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

export function usePrivateMode(options: UsePrivateModeOptions = {}): PrivateModeController {
  const [booting, setBooting] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [bundle, setBundle] = useState<PrivateBundle | null>(null);
  const [manifest, setManifest] = useState<ModelManifest | null>(null);
  const [bundleSha256, setBundleSha256] = useState<string | null>(null);
  const [bundleBytes, setBundleBytes] = useState(0);
  const [environment, setEnvironment] = useState<EnvironmentReport | null>(null);
  const [capability, setCapability] = useState<CapabilityAssessment | null>(null);
  const [settings, setSettings] = useState<PrivateSettings | null>(null);
  const [models, setModels] = useState<ModelState[]>([]);
  const [usageBytes, setUsageBytes] = useState(0);
  const [progress, setProgress] = useState<Record<string, string>>({});
  // Bumped whenever the transcript store changes behind React's back (create,
  // unlock, lock, wipe), so `locked` is re-read on the next render.
  const [, bumpStorage] = useState(0);

  const databaseRef = useRef<PrivateDatabase | null>(null);
  const settingsRef = useRef<SettingsStore | null>(null);
  const transcriptRef = useRef<TranscriptStore | null>(null);
  const managerRef = useRef<DownloadManager | null>(null);
  const sessionRef = useRef<PrivateSession | null>(null);
  const conversationRef = useRef<string>(conversationId());
  const onReadyRef = useRef(options.onReady);
  onReadyRef.current = options.onReady;

  // ------------------------------------------------------------- boot

  useEffect(() => {
    let cancelled = false;
    async function boot(): Promise<void> {
      try {
        const [bundleAsset, manifestAsset, report] = await Promise.all([
          loadSafetyBundle({ fetchImpl: options.fetchImpl }),
          loadModelManifest({ fetchImpl: options.fetchImpl }),
          collectEnvironment(),
        ]);
        if (cancelled) {
          return;
        }
        const verdict = assess(report);
        const database = await openPrivateDatabase();
        const settingsStore = new SettingsStore(database);
        const transcript = new TranscriptStore(database, settingsStore);
        const manager = new DownloadManager({
          manifest: manifestAsset.value,
          sink: new IndexedDbSink(database),
          fetcher: new HttpFetcher({ fetchImpl: options.fetchImpl }),
          hasher: new WebCryptoHasher(),
          store: new IndexedDbKeyValue(database),
          freeBytes: freeStorageBytes,
          minFreeBytes: 256 * 1024 * 1024,
        });
        if (cancelled) {
          return;
        }
        databaseRef.current = database;
        settingsRef.current = settingsStore;
        transcriptRef.current = transcript;
        managerRef.current = manager;

        const current = await settingsStore.load();
        const states = await manager.states();
        const usage = await manager.usageBytes();
        if (cancelled) {
          return;
        }
        setBundle(bundleAsset.value);
        setManifest(manifestAsset.value);
        setBundleSha256(bundleAsset.sha256);
        setBundleBytes(bundleAsset.bytes);
        setEnvironment(report);
        setCapability(verdict);
        setSettings(current);
        setModels(states);
        setUsageBytes(usage);
        setBooting(false);
        onReadyRef.current?.({
          booting: false,
          error: null,
          bundle: bundleAsset.value,
          manifest: manifestAsset.value,
          bundleSha256: bundleAsset.sha256,
          bundleBytes: bundleAsset.bytes,
          environment: report,
          capability: verdict,
          settings: current,
          models: states,
          usageBytes: usage,
        });
      } catch (bootError) {
        if (cancelled) {
          return;
        }
        setError(bootError instanceof Error ? bootError.message : String(bootError));
        setBooting(false);
      }
    }
    void boot();
    return () => {
      cancelled = true;
    };
    // The boot runs once per mount; `fetchImpl` is a test seam, not a dependency.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ------------------------------------------------------------- derived

  const enabled = settings?.privateMode ?? false;
  const locked = transcriptRef.current?.locked ?? true;
  const hasKey = settings?.verifier !== null && settings?.verifier !== undefined;

  const replyRuntime: ReplyModel | null = useMemo(() => {
    if (bundle === null || manifest === null || capability === null) {
      return null;
    }
    const patterns = compilePatternSet(bundle);
    const guard = buildOutputGuard(bundle, patterns);
    const template = new TemplateReplyModel({ bundle, guard });
    const replyEntry = manifest.models.find((model) => model.id === "reply");
    const installed = models.find((model) => model.modelId === "reply");
    if (
      capability.supported.replies &&
      replyEntry !== undefined &&
      installBlocker(replyEntry) === null &&
      installed?.phase === "ready"
    ) {
      // The real model, with the template writer behind it: a GPU that dies mid
      // conversation must not leave somebody without an answer.
      return new WebLlmReplyModel({ entry: replyEntry, bundle });
    }
    return template;
    // `patterns`/`guard` are cheap to rebuild and depend only on the bundle.
  }, [bundle, manifest, capability, models]);

  const session = useMemo<PrivateSession | null>(() => {
    // No session exists while Private Mode is off: `ChatView` picks its controller
    // from `enabled`, and a session that could answer while off would be a claim
    // this feature must not make.
    if (!enabled) {
      return null;
    }
    if (
      bundle === null ||
      replyRuntime === null ||
      transcriptRef.current === null ||
      settingsRef.current === null
    ) {
      return null;
    }
    const patterns = compilePatternSet(bundle);
    const template =
      replyRuntime instanceof TemplateReplyModel
        ? replyRuntime
        : new TemplateReplyModel({ bundle, guard: buildOutputGuard(bundle, patterns) });
    return new PrivateSession({
      engine: new RuleEngine(patterns),
      escalator: buildEscalator(bundle),
      guard: buildOutputGuard(bundle, patterns),
      replies: replyRuntime,
      fallbackReplies: template,
      transcript: transcriptRef.current,
      settings: settingsRef.current,
      conversationId: conversationRef.current,
    });
    // `enabled` is a dependency; `settings` is not, beyond that boolean:
    // `PrivateSession` holds the `SettingsStore` and reads locale, region and
    // storeTranscript live on every turn, so an unrelated settings change must not
    // rebuild (and reset) the session and its in-memory history.
  }, [bundle, replyRuntime, enabled]);

  sessionRef.current = session;

  // ------------------------------------------------------------- actions

  const patchSettings = useCallback(async (patch: Parameters<SettingsStore["save"]>[0]) => {
    const store = settingsRef.current;
    if (store === null) {
      return;
    }
    setSettings(await store.save(patch));
  }, []);

  /** Re-read settings and force a render after the transcript store changed. */
  const refreshStorage = useCallback(async () => {
    const store = settingsRef.current;
    if (store !== null) {
      setSettings(await store.load());
    }
    bumpStorage((version) => version + 1);
  }, []);

  const refreshModels = useCallback(async () => {
    const manager = managerRef.current;
    if (manager === null) {
      return;
    }
    setModels(await manager.states());
    setUsageBytes(await manager.usageBytes());
  }, []);

  const listen = useCallback(
    (modelId: ModelId) =>
      (update: {
        phase: string;
        receivedBytes: number;
        totalBytes: number;
        bytesPerSecond: number | null;
        file?: string;
      }) => {
        const percent =
          update.totalBytes > 0 ? Math.round((update.receivedBytes / update.totalBytes) * 100) : 0;
        setProgress((current) => ({
          ...current,
          [modelId]: `${update.phase} ${percent}%${update.file === undefined ? "" : ` — ${update.file}`}`,
        }));
      },
    [],
  );

  const setEnabled = useCallback(
    async (value: boolean) => {
      await patchSettings({
        privateMode: value,
        consent: value ? { capabilityCheckedAt: new Date().toISOString() } : {},
      });
      if (!value) {
        transcriptRef.current?.lock();
        sessionRef.current = null;
      }
    },
    [patchSettings],
  );

  const consentAndDownload = useCallback(
    async (modelId: ModelId) => {
      const manager = managerRef.current;
      if (manager === null) {
        return;
      }
      await manager.consent(modelId);
      await patchSettings({ consent: { modelsAt: new Date().toISOString() } });
      await manager.download(modelId, listen(modelId));
      setProgress((current) => {
        const next = { ...current };
        delete next[modelId];
        return next;
      });
      await refreshModels();
    },
    [listen, patchSettings, refreshModels],
  );

  const pause = useCallback(
    async (modelId: ModelId) => {
      await managerRef.current?.pause(modelId);
      await refreshModels();
    },
    [refreshModels],
  );

  const cancel = useCallback(
    async (modelId: ModelId) => {
      await managerRef.current?.cancel(modelId);
      await refreshModels();
    },
    [refreshModels],
  );

  const verify = useCallback(
    async (modelId: ModelId) => {
      const result = await managerRef.current?.verify(modelId);
      await refreshModels();
      return { ok: result?.ok ?? false };
    },
    [refreshModels],
  );

  const deleteModel = useCallback(
    async (modelId: ModelId) => {
      await managerRef.current?.deleteModel(modelId);
      await refreshModels();
    },
    [refreshModels],
  );

  const deleteAllModels = useCallback(async () => {
    const result = await managerRef.current?.deleteAll();
    await refreshModels();
    return result?.freedBytes ?? 0;
  }, [refreshModels]);

  const createKey = useCallback(
    async (passphrase: string) => {
      const transcript = transcriptRef.current;
      if (transcript === null) {
        return { ok: false, advice: "storage is not available in this browser" };
      }
      const result = await transcript.createKey(passphrase);
      // A new key changes `hasKey`, which the panel reads from settings.
      await refreshStorage();
      return result;
    },
    [refreshStorage],
  );

  const unlock = useCallback(
    async (passphrase: string) => {
      const transcript = transcriptRef.current;
      if (transcript === null) {
        return { ok: false, reason: "no-storage" };
      }
      const result = await transcript.unlock(passphrase);
      await refreshStorage();
      return result;
    },
    [refreshStorage],
  );

  const lock = useCallback(() => {
    transcriptRef.current?.lock();
    sessionRef.current?.reset();
    bumpStorage((version) => version + 1);
  }, []);

  const wipe = useCallback(async () => {
    const result = await transcriptRef.current?.wipe();
    await refreshStorage();
    await refreshModels();
    return result?.messages ?? 0;
  }, [refreshModels, refreshStorage]);

  const setRegion = useCallback(
    async (region: string | null) => patchSettings({ region }),
    [patchSettings],
  );

  const setLocale = useCallback(
    async (locale: ChatLocale) => patchSettings({ locale }),
    [patchSettings],
  );

  const setStoreTranscript = useCallback(
    async (value: boolean) => patchSettings({ storeTranscript: value }),
    [patchSettings],
  );

  return {
    booting,
    error,
    bundle,
    manifest,
    bundleSha256,
    bundleBytes,
    environment,
    capability,
    settings,
    models,
    usageBytes,
    enabled,
    locked,
    hasKey,
    progress,
    session,
    setEnabled,
    setRegion,
    setLocale,
    setStoreTranscript,
    consentAndDownload,
    pause,
    cancel,
    verify,
    deleteModel,
    deleteAllModels,
    createKey,
    unlock,
    lock,
    wipe,
  };
}
