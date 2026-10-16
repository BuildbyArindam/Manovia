/**
 * The Private Mode panel: one toggle, and everything that toggle depends on.
 *
 * Layout follows the order a reader has to understand things in:
 *
 *   1. **the switch**, with the two honest hints ("on — read and answered on this
 *      device" / "off — messages go to the server");
 *   2. **the capability verdict** — one translated sentence per tier, the offer of
 *      standard mode when this device cannot do the interesting part, the
 *      "smaller model, shorter replies" notice, and the probe's own English
 *      explanation collapsed under "why, in detail";
 *   3. **the model manager** — size and honest note *before* consent, progress in a
 *      live region, pause/resume/cancel, integrity check, per-model delete and
 *      delete-all;
 *   4. **storage** — the passphrase, with the sentence that says it is never stored
 *      and cannot be recovered, plus lock and delete-everything.
 *
 * Nothing here is hidden behind an icon: consent, size and delete are the three
 * controls a privacy surface must not make hard to find (scope item 3).
 */

import { useCallback, useId, useState, type ReactElement } from "react";

import { formatBytes, summarise } from "../runtime/capability";
import { installBlocker } from "../models/manifest";
import type { ModelEntry } from "../models/manifest";
import type { ModelState } from "../models/downloadManager";
import type { PrivateModeController } from "../session/usePrivateMode";
import { PRIVATE_COPY } from "./copy";

export interface PrivateModePanelProps {
  controller: PrivateModeController;
}

const BUTTON =
  "inline-flex min-h-11 items-center rounded-xl border border-border-strong px-3 py-2 text-sm font-semibold";
const DANGER = `${BUTTON} border-danger text-danger`;

export function PrivateModePanel({ controller }: PrivateModePanelProps): ReactElement {
  const {
    booting,
    error,
    manifest,
    capability,
    environment,
    settings,
    models,
    usageBytes,
    enabled,
    locked,
    hasKey,
    progress,
    setEnabled,
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
  } = controller;

  const headingId = useId();
  const ledeId = useId();
  const nameId = useId();
  const stateId = useId();
  const progressId = useId();
  const passphraseId = useId();

  const [passphrase, setPassphrase] = useState("");
  const [passphraseNote, setPassphraseNote] = useState<string | null>(null);
  const [confirmingWipe, setConfirmingWipe] = useState(false);
  const [confirmingDeleteAll, setConfirmingDeleteAll] = useState(false);

  const tierSentence =
    capability === null
      ? null
      : capability.tier === "full"
        ? PRIVATE_COPY.tierFull
        : capability.tier === "classifiers"
          ? PRIVATE_COPY.tierClassifiers
          : PRIVATE_COPY.tierRules;

  const progressLines = Object.entries(progress).filter(([, value]) => value !== "");

  const stateFor = useCallback(
    (id: string): ModelState | undefined => models.find((model) => model.modelId === id),
    [models],
  );

  async function handleToggle(next: boolean): Promise<void> {
    await setEnabled(next);
  }

  async function handleCreateKey(): Promise<void> {
    const result = await createKey(passphrase);
    setPassphraseNote(result.ok ? null : result.advice);
    if (result.ok) {
      setPassphrase("");
    }
  }

  async function handleUnlock(): Promise<void> {
    const result = await unlock(passphrase);
    setPassphraseNote(result.ok ? null : (result.reason ?? "could not unlock"));
    if (result.ok) {
      setPassphrase("");
    }
  }

  async function handleWipe(): Promise<void> {
    await wipe();
    setConfirmingWipe(false);
    setPassphraseNote(null);
  }

  async function handleDeleteAll(): Promise<void> {
    await deleteAllModels();
    setConfirmingDeleteAll(false);
  }

  return (
    <section
      aria-labelledby={headingId}
      data-testid="private-mode-panel"
      className="rounded-2xl border border-border bg-surface p-4 shadow-soft"
    >
      <h2 id={headingId} className="text-lg font-semibold">
        {PRIVATE_COPY.panelHeading}
      </h2>
      <p id={ledeId} className="mt-1 text-sm text-ink-muted">
        {PRIVATE_COPY.panelLede}
      </p>

      <label className="mt-3 inline-flex cursor-pointer items-start gap-3 rounded-xl border border-border-strong px-3 py-2">
        <input
          type="checkbox"
          role="switch"
          data-testid="private-mode-toggle"
          checked={enabled}
          disabled={booting}
          aria-labelledby={nameId}
          aria-describedby={`${ledeId} ${stateId}`}
          onChange={(event) => {
            void handleToggle(event.target.checked);
          }}
          className="mt-1 h-5 w-5 accent-accent-bg"
        />
        <span>
          <span id={nameId} className="block font-semibold">
            {PRIVATE_COPY.enable}
          </span>
          {/* The state, not part of the name: a switch whose accessible name
              changed with its own value would read as a different control. */}
          <span id={stateId} className="block text-sm text-ink-muted">
            {enabled ? PRIVATE_COPY.enabledHint : PRIVATE_COPY.disabledHint}
          </span>
        </span>
      </label>

      {booting ? (
        <p data-testid="private-booting" className="mt-3 text-sm text-ink-muted">
          {PRIVATE_COPY.booting}
        </p>
      ) : null}

      {error === null ? null : (
        <p data-testid="private-boot-error" role="alert" className="mt-3 text-sm text-danger">
          {PRIVATE_COPY.bootFailed}: {error}
        </p>
      )}

      {enabled && tierSentence !== null ? (
        <div className="mt-4 space-y-2 text-sm" data-testid="private-capability">
          <p data-testid="private-tier" className="font-semibold">
            {tierSentence}
          </p>
          {/* The offer is a sentence, not a button: the switch that returns the
              reader to standard mode is the one at the top of this panel. */}
          {capability?.offerStandardMode === true ? (
            <p data-testid="private-offer-standard">{PRIVATE_COPY.offerStandard}</p>
          ) : null}
          <p data-testid="private-smaller-model" className="text-ink-muted">
            {PRIVATE_COPY.smallerModel}
          </p>
          {capability === null || environment === null ? null : (
            <details data-testid="private-capability-details" className="text-ink-muted">
              <summary className="cursor-pointer font-semibold">{PRIVATE_COPY.whyHeading}</summary>
              <p className="mt-2">{capability.explanation}</p>
              <p className="mt-1 font-mono text-xs">{summarise(environment)}</p>
            </details>
          )}
        </div>
      ) : null}

      {/* One live region for every download, so a screen reader hears progress
          without three competing announcements. */}
      <p
        id={progressId}
        role="status"
        aria-live="polite"
        data-testid="private-progress"
        className="sr-only"
      >
        {progressLines.map(([id, line]) => `${id}: ${line}`).join(" — ")}
      </p>

      {enabled && manifest !== null ? (
        <div className="mt-5 border-t border-border pt-4">
          <h3 className="font-semibold">{PRIVATE_COPY.modelsHeading}</h3>
          <p className="mt-1 text-sm text-ink-muted" data-testid="private-models-lede">
            {PRIVATE_COPY.modelsLede}
          </p>
          <ul className="mt-3 space-y-3">
            {manifest.models.map((entry) => (
              <ModelRow
                key={entry.id}
                entry={entry}
                state={stateFor(entry.id)}
                onDownload={() => void consentAndDownload(entry.id)}
                onPause={() => void pause(entry.id)}
                onCancel={() => void cancel(entry.id)}
                onVerify={() => void verify(entry.id)}
                onDelete={() => void deleteModel(entry.id)}
              />
            ))}
          </ul>
          {usageBytes === 0 ? null : confirmingDeleteAll ? (
            <p className="mt-3 text-sm">
              <span data-testid="private-delete-all-confirm">{PRIVATE_COPY.wipeConfirm}</span>{" "}
              <button
                type="button"
                data-testid="private-delete-all-confirm-button"
                onClick={() => void handleDeleteAll()}
                className={DANGER}
              >
                {PRIVATE_COPY.modelDelete}
              </button>
            </p>
          ) : (
            <button
              type="button"
              data-testid="private-delete-all"
              onClick={() => setConfirmingDeleteAll(true)}
              className={`${DANGER} mt-3`}
            >
              {PRIVATE_COPY.modelDeleteAll(formatBytes(usageBytes))}
            </button>
          )}
        </div>
      ) : null}

      {enabled ? (
        <div className="mt-5 border-t border-border pt-4" data-testid="private-storage">
          <h3 className="font-semibold">{PRIVATE_COPY.storageHeading}</h3>
          <p className="mt-1 text-sm text-ink-muted">{PRIVATE_COPY.storageLede}</p>

          <label className="mt-3 inline-flex cursor-pointer items-start gap-3 rounded-xl border border-border-strong px-3 py-2">
            <input
              type="checkbox"
              role="switch"
              data-testid="private-store-transcript"
              checked={settings?.storeTranscript ?? false}
              disabled={!hasKey || locked}
              onChange={(event) => {
                void setStoreTranscript(event.target.checked);
              }}
              className="mt-1 h-5 w-5 accent-accent-bg"
            />
            <span className="block font-semibold">{PRIVATE_COPY.storeTranscript}</span>
          </label>

          {hasKey && !locked ? (
            <div className="mt-3 flex flex-wrap gap-2">
              <button
                type="button"
                data-testid="private-lock"
                onClick={() => lock()}
                className={BUTTON}
              >
                {PRIVATE_COPY.lock}
              </button>
              {confirmingWipe ? (
                <span className="text-sm">
                  <span data-testid="private-wipe-confirm">{PRIVATE_COPY.wipeConfirm}</span>{" "}
                  <button
                    type="button"
                    data-testid="private-wipe-confirm-button"
                    onClick={() => void handleWipe()}
                    className={DANGER}
                  >
                    {PRIVATE_COPY.wipe}
                  </button>
                </span>
              ) : (
                <button
                  type="button"
                  data-testid="private-wipe"
                  onClick={() => setConfirmingWipe(true)}
                  className={DANGER}
                >
                  {PRIVATE_COPY.wipe}
                </button>
              )}
            </div>
          ) : (
            <div className="mt-3 flex flex-wrap items-end gap-2">
              <label className="flex flex-col gap-1 text-sm">
                <span className="font-semibold">{PRIVATE_COPY.passphraseLabel}</span>
                <input
                  id={passphraseId}
                  data-testid="private-passphrase"
                  type="password"
                  autoComplete="off"
                  value={passphrase}
                  onChange={(event) => setPassphrase(event.target.value)}
                  className="min-h-11 rounded-xl border border-border-strong bg-surface px-3 py-2"
                />
              </label>
              <button
                type="button"
                data-testid={hasKey ? "private-unlock" : "private-create-key"}
                disabled={passphrase.length === 0}
                onClick={() => void (hasKey ? handleUnlock() : handleCreateKey())}
                className={BUTTON}
              >
                {hasKey ? PRIVATE_COPY.passphraseUnlock : PRIVATE_COPY.passphraseCreate}
              </button>
              {passphraseNote === null ? null : (
                <p
                  data-testid="private-passphrase-note"
                  role="alert"
                  className="text-sm text-danger"
                >
                  {passphraseNote}
                </p>
              )}
            </div>
          )}
        </div>
      ) : null}
    </section>
  );
}

interface ModelRowProps {
  entry: ModelEntry;
  state: ModelState | undefined;
  onDownload: () => void;
  onPause: () => void;
  onCancel: () => void;
  onVerify: () => void;
  onDelete: () => void;
}

function ModelRow({
  entry,
  state,
  onDownload,
  onPause,
  onCancel,
  onVerify,
  onDelete,
}: ModelRowProps): ReactElement {
  const phase = state?.phase ?? "not-downloaded";
  const blocker = installBlocker(entry);
  const downloading = phase === "downloading";
  const resumable = phase === "paused";
  const downloaded = state !== undefined && state.receivedBytes > 0;
  const percent =
    state !== undefined && state.totalBytes > 0
      ? Math.round((state.receivedBytes / state.totalBytes) * 100)
      : null;
  // A model with no files (the unshipped safety slot) is not "verified": an empty
  // list satisfies `every`, which would put a trust claim on a model that does not
  // exist yet.
  const verified = state !== undefined && state.files.length > 0 && state.files.every((file) => file.verified);

  return (
    <li
      data-testid={`model-${entry.id}`}
      data-phase={phase}
      className="rounded-xl border border-border-strong px-3 py-2"
    >
      <p className="font-semibold">{entry.label}</p>
      {/* The honest caveat ships with the manifest, not with the UI, so the
          claim about a model is reviewed where the model is chosen. */}
      <p className="text-sm text-ink-muted" data-testid={`model-${entry.id}-honest`}>
        {entry.honest_note}
      </p>
      <p className="text-sm text-ink-muted" data-testid={`model-${entry.id}-size`}>
        {formatBytes(entry.total_bytes)}
      </p>
      <p className="text-sm" data-testid={`model-${entry.id}-phase`}>
        {PRIVATE_COPY.phase(phase)}
        {percent === null ? "" : ` — ${percent}%`}
      </p>
      {state?.error === null || state?.error === undefined ? null : (
        <p className="text-sm text-danger" data-testid={`model-${entry.id}-error`}>
          {state.error.code}: {state.error.detail}
        </p>
      )}
      {blocker === null ? null : (
        <p className="text-sm text-ink-muted" data-testid={`model-${entry.id}-blocker`}>
          {PRIVATE_COPY.modelBlocker(blocker)}
        </p>
      )}
      {verified ? (
        <p className="text-sm text-ink-muted" data-testid={`model-${entry.id}-verified`}>
          {PRIVATE_COPY.modelVerified}
        </p>
      ) : null}

      <div className="mt-2 flex flex-wrap gap-2">
        {blocker === null &&
        (phase === "not-downloaded" || phase === "failed" || phase === "deleted") ? (
          <button
            type="button"
            data-testid={`model-${entry.id}-download`}
            onClick={onDownload}
            className={BUTTON}
          >
            {PRIVATE_COPY.modelConsent(entry.label, formatBytes(entry.total_bytes))}
          </button>
        ) : null}
        {downloading ? (
          <>
            <button
              type="button"
              data-testid={`model-${entry.id}-pause`}
              onClick={onPause}
              className={BUTTON}
            >
              {PRIVATE_COPY.modelPause}
            </button>
            <button
              type="button"
              data-testid={`model-${entry.id}-cancel`}
              onClick={onCancel}
              className={BUTTON}
            >
              {PRIVATE_COPY.modelCancel}
            </button>
          </>
        ) : null}
        {resumable ? (
          <>
            <button
              type="button"
              data-testid={`model-${entry.id}-resume`}
              onClick={onDownload}
              className={BUTTON}
            >
              {PRIVATE_COPY.modelResume}
            </button>
            <button
              type="button"
              data-testid={`model-${entry.id}-cancel`}
              onClick={onCancel}
              className={BUTTON}
            >
              {PRIVATE_COPY.modelCancel}
            </button>
          </>
        ) : null}
        {phase === "ready" ? (
          <button
            type="button"
            data-testid={`model-${entry.id}-verify`}
            onClick={onVerify}
            className={BUTTON}
          >
            {PRIVATE_COPY.modelVerify}
          </button>
        ) : null}
        {downloaded ? (
          <button
            type="button"
            data-testid={`model-${entry.id}-delete`}
            onClick={onDelete}
            className={DANGER}
          >
            {PRIVATE_COPY.modelDelete}
          </button>
        ) : null}
      </div>
    </li>
  );
}
