/**
 * Settings → Privacy.
 *
 * The screen exists to answer four questions honestly, in this order:
 *
 * 1. **What is kept, and where?** A plain-language list, including the parts
 *    that are *not* stored (message text is never in the logs; a safety event
 *    keeps a level and a category, never the sentence that caused it).
 * 2. **What can I change?** The three settings that actually change behaviour —
 *    saving chats, keeping derived sentiment, and how long anything is kept.
 * 3. **What can I take?** The export: one button, one file, everything
 *    decrypted for its owner.
 * 4. **What can I destroy?** Deletion, behind a typed confirmation and, for an
 *    account with a password, the password itself.
 *
 * Everything here reports what the *server* did, not what the UI hoped: the
 * deletion receipt is rendered from the response, and a failed export says so
 * rather than leaving an empty file behind.
 */

import { useState, type ReactElement } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { isApiError, type ApiClient } from "@/lib/api";
import { t } from "@/lib/i18n/i18n";
import { api as defaultClient } from "@/lib/api";
import { formatDateTime } from "@/lib/dates";
import { useOnboarding } from "@/onboarding/OnboardingProvider";

import {
  deleteAccount,
  downloadExport,
  fetchConsentHistory,
  fetchPrivacySettings,
  savePrivacySettings,
} from "./endpoints";
import type { DeletionReceipt, PrivacySettings } from "./types";

export const privacyKeys = {
  settings: ["privacy", "settings"] as const,
  consents: ["privacy", "consents"] as const,
};

/** The phrase the backend insists on. Kept in one place so the copy and the
 * request cannot drift apart. */
export const DELETE_CONFIRMATION = "DELETE";

const CONSENT_LABEL_KEYS: Record<string, string> = {
  terms: "privacy.consentTerms",
  privacy: "privacy.consentPrivacy",
  ai_disclosure: "privacy.consentAi",
  store_chat: "privacy.consentStoreChat",
};

function consentLabel(kind: string): string {
  const key = CONSENT_LABEL_KEYS[kind];
  return key !== undefined ? t(key) : kind;
}

/** Plain-language: what is stored, where it lives, and how it is protected. */
function storageFacts(): ReadonlyArray<{ title: string; detail: string }> {
  return [
    { title: t("privacy.storageDeviceTitle"), detail: t("privacy.storageDeviceDetail") },
    { title: t("privacy.storageEncryptedTitle"), detail: t("privacy.storageEncryptedDetail") },
    { title: t("privacy.storageNumbersTitle"), detail: t("privacy.storageNumbersDetail") },
    { title: t("privacy.storageSafetyTitle"), detail: t("privacy.storageSafetyDetail") },
  ];
}

function describeKeptFor(days: number | null): string {
  if (days === null) {
    return t("privacy.retentionForever");
  }
  if (days === 30) {
    return t("privacy.retention30");
  }
  if (days === 90) {
    return t("privacy.retention90");
  }
  if (days === 180) {
    return t("privacy.retention180");
  }
  if (days === 365) {
    return t("privacy.retention365");
  }
  return t("privacy.retentionGeneric", { days });
}

export interface PrivacySectionProps {
  client?: ApiClient;
  /** Called after a successful deletion, once the session has been cleared. */
  onDeleted?: (receipt: DeletionReceipt) => void;
}

export function PrivacySection({
  client = defaultClient,
  onDeleted,
}: PrivacySectionProps): ReactElement {
  const queryClient = useQueryClient();
  const { record, reset } = useOnboarding();
  const [message, setMessage] = useState<string | null>(null);
  const [typedConfirmation, setTypedConfirmation] = useState("");
  const [password, setPassword] = useState("");
  const [receipt, setReceipt] = useState<DeletionReceipt | null>(null);

  const settingsQuery = useQuery<PrivacySettings, Error>({
    queryKey: privacyKeys.settings,
    queryFn: () => fetchPrivacySettings(client),
  });
  const consentsQuery = useQuery({
    queryKey: privacyKeys.consents,
    queryFn: () => fetchConsentHistory(client),
  });

  const save = useMutation<PrivacySettings, Error, Partial<PrivacySettings>>({
    mutationFn: (patch) => savePrivacySettings(patch, client),
    onSuccess: async (saved) => {
      setMessage(t("privacy.saved"));
      queryClient.setQueryData(privacyKeys.settings, saved);
      await queryClient.invalidateQueries({ queryKey: privacyKeys.settings });
    },
    onError: (error) => {
      setMessage(
        isApiError(error)
          ? t("privacy.saveErrorApi", { code: error.code })
          : t("privacy.saveErrorGeneric"),
      );
    },
  });

  const exportData = useMutation<{ filename: string }, Error, void>({
    mutationFn: async () => {
      const { blob, filename } = await downloadExport(client);
      saveBlob(blob, filename);
      return { filename };
    },
    onSuccess: ({ filename }) => {
      setMessage(t("privacy.exportDone", { filename }));
    },
    onError: (error) => {
      setMessage(
        isApiError(error)
          ? t("privacy.exportErrorApi", { code: error.code })
          : t("privacy.exportErrorGeneric"),
      );
    },
  });

  const deleteAccountMutation = useMutation<DeletionReceipt, Error, void>({
    mutationFn: () =>
      deleteAccount(
        password.trim() === ""
          ? { confirm: typedConfirmation }
          : { confirm: typedConfirmation, password },
        client,
      ),
    onSuccess: (result) => {
      setReceipt(result);
      setMessage(t("privacy.deletedMessage"));
      client.clearSession();
      reset();
      onDeleted?.(result);
    },
    onError: (error) => {
      if (isApiError(error)) {
        setMessage(deletionErrorMessage(error.code, error.message));
        return;
      }
      setMessage(t("privacy.deleteFailed"));
    },
  });

  const settings = settingsQuery.data;
  const canDelete = typedConfirmation.trim().toUpperCase() === DELETE_CONFIRMATION;

  return (
    <section
      aria-labelledby="privacy-heading"
      className="rounded-2xl border border-border bg-surface p-6 shadow-soft"
      data-testid="privacy-section"
    >
      <h2 id="privacy-heading" className="text-lg">
        {t("privacy.heading")}
      </h2>
      <p className="mt-2 text-sm text-ink-muted">
        {t("privacy.lede")}
      </p>

      <div className="mt-4 space-y-3">
        {storageFacts().map((fact) => (
          <div key={fact.title} className="rounded-xl bg-surface-muted p-4">
            <p className="font-semibold">{fact.title}</p>
            <p className="mt-1 text-sm text-ink-muted">{fact.detail}</p>
          </div>
        ))}
      </div>

      {settingsQuery.isLoading ? (
        <p className="mt-5 text-sm text-ink-muted" role="status">
          {t("privacy.loadingSettings")}
        </p>
      ) : settingsQuery.isError || settings === undefined ? (
        <p className="mt-5 text-sm text-ink-muted" role="alert">
          {t("privacy.loadSettingsError")}
        </p>
      ) : (
        <fieldset className="mt-5 space-y-4" disabled={save.isPending}>
          <legend className="font-semibold">{t("privacy.choicesLegend")}</legend>

          <label className="flex cursor-pointer items-start gap-3 rounded-xl border border-border-strong px-4 py-3">
            <input
              type="checkbox"
              role="switch"
              data-testid="store-chat-toggle"
              checked={settings.store_chat}
              onChange={(event) => save.mutate({ store_chat: event.target.checked })}
              className="mt-1 h-5 w-5 accent-accent-bg"
            />
            <span>
              <span className="block font-semibold">{t("privacy.storeChatLabel")}</span>
              <span className="block text-sm text-ink-muted">
                {t("privacy.storeChatHelp")}
              </span>
            </span>
          </label>

          <label className="flex cursor-pointer items-start gap-3 rounded-xl border border-border-strong px-4 py-3">
            <input
              type="checkbox"
              role="switch"
              data-testid="store-sentiment-toggle"
              checked={settings.store_sentiment}
              onChange={(event) => save.mutate({ store_sentiment: event.target.checked })}
              className="mt-1 h-5 w-5 accent-accent-bg"
            />
            <span>
              <span className="block font-semibold">{t("privacy.storeSentimentLabel")}</span>
              <span className="block text-sm text-ink-muted">
                {t("privacy.storeSentimentHelp")}
              </span>
            </span>
          </label>

          <label className="block">
            <span className="font-semibold">{t("privacy.retentionLegend")}</span>
            <select
              data-testid="retention-select"
              value={settings.retention_days === null ? "forever" : String(settings.retention_days)}
              onChange={(event) => {
                const value = event.target.value;
                save.mutate({ retention_days: value === "forever" ? null : Number(value) });
              }}
              className="mt-2 block rounded-xl border border-border-strong bg-surface px-3 py-2"
            >
              <option value="forever">{t("privacy.retentionForeverOption")}</option>
              {settings.retention_choices.map((days) => (
                <option key={days} value={String(days)}>
                  {describeKeptFor(days)}
                </option>
              ))}
            </select>
            <span className="mt-2 block text-sm text-ink-muted">
              {t("privacy.retentionHelp")}{" "}
              {settings.been_saved ? "" : t("privacy.retentionDefaultsNote")}
            </span>
          </label>
        </fieldset>
      )}

      <div className="mt-6 border-t border-border pt-5">
        <h3 className="font-semibold">{t("privacy.exportHeading")}</h3>
        <p className="mt-1 text-sm text-ink-muted">
          {t("privacy.exportHelp")}
        </p>
        <button
          type="button"
          data-testid="export-button"
          disabled={!settingsQuery.isSuccess || exportData.isPending}
          onClick={() => exportData.mutate()}
          className="mt-3 rounded-xl bg-accent-bg px-4 py-2 text-sm font-semibold text-accent-fg disabled:opacity-60"
        >
          {exportData.isPending ? t("privacy.exportPreparing") : t("privacy.exportButton")}
        </button>
      </div>

      <div className="mt-6 border-t border-border pt-5">
        <h3 className="font-semibold">{t("privacy.consentsHeading")}</h3>
        {consentsQuery.isLoading ? (
          <p className="mt-2 text-sm text-ink-muted" role="status">
            {t("privacy.consentsLoading")}
          </p>
        ) : consentsQuery.isError ? (
          <p className="mt-2 text-sm text-ink-muted" role="alert">
            {t("privacy.consentsLoadError")}
          </p>
        ) : (consentsQuery.data?.items.length ?? 0) === 0 ? (
          <p className="mt-2 text-sm text-ink-muted">{t("privacy.consentsEmpty")}</p>
        ) : (
          <ul className="mt-3 space-y-2" data-testid="consent-history">
            {consentsQuery.data?.items.map((item) => (
              <li
                key={`${item.kind}-${item.version}-${item.created_at}`}
                className="flex flex-wrap items-baseline justify-between gap-2 rounded-xl bg-surface-muted px-4 py-3"
              >
                <span className="font-medium">{consentLabel(item.kind)}</span>
                <span className="text-sm text-ink-muted">
                  {item.granted ? t("privacy.consentAgreed") : t("privacy.consentWithdrawn")} ·{" "}
                  {t("privacy.consentVersion", { version: item.version })} ·{" "}
                  {formatDateTime(item.created_at)}
                </span>
              </li>
            ))}
          </ul>
        )}
        <p className="mt-2 text-sm text-ink-muted">
          {t("privacy.consentsNote")}
        </p>
      </div>

      <div className="mt-6 border-t border-border pt-5">
        <h3 className="font-semibold">{t("privacy.anonymousHeading")}</h3>
        <p className="mt-2 text-sm text-ink-muted" data-testid="anonymous-mode-copy">
          {record === null
            ? t("privacy.anonymousSignedOut")
            : record.user.isAnonymous
              ? t("privacy.anonymousActive")
              : t("privacy.anonymousEmail")}
        </p>
        <p className="mt-2 text-sm text-ink-muted">
          {t("privacy.anonymousCaveat")}
        </p>
      </div>

      <div className="mt-6 border-t border-border pt-5">
        <h3 className="font-semibold">{t("privacy.deleteHeading")}</h3>
        <p className="mt-2 text-sm text-ink-muted">
          {t("privacy.deleteHelp")}
        </p>
        {receipt !== null ? (
          <div
            role="status"
            data-testid="deletion-receipt"
            className="mt-3 rounded-xl border border-border-strong bg-surface-muted p-4"
          >
            <p className="font-semibold">{t("privacy.receiptHeading")}</p>
            <p className="mt-1 text-sm text-ink-muted">
              {t("privacy.receiptLine", { ref: receipt.account_ref, rows: receipt.total_rows })}{" "}
              {formatDateTime(receipt.created_at)}
            </p>
            <ul className="mt-2 text-sm text-ink-muted">
              {Object.entries(receipt.rows_deleted)
                .filter(([, count]) => count > 0)
                .map(([table, count]) => (
                  <li key={table}>
                    {t("privacy.receiptCountLine", { count, table })}
                  </li>
                ))}
            </ul>
            <p className="mt-2 text-sm text-ink-muted">
              {t("privacy.receiptNote")}
            </p>
          </div>
        ) : (
          <>
            <label className="mt-3 block">
              <span className="text-sm font-semibold">{t("privacy.typeToConfirm", { word: DELETE_CONFIRMATION })}</span>
              <input
                type="text"
                data-testid="delete-confirmation-input"
                value={typedConfirmation}
                onChange={(event) => setTypedConfirmation(event.target.value)}
                autoComplete="off"
                className="mt-1 block w-48 rounded-xl border border-border-strong bg-surface px-3 py-2"
              />
            </label>
            {record?.user.isAnonymous === false ? (
              <label className="mt-3 block">
                <span className="text-sm font-semibold">{t("privacy.passwordLabel")}</span>
                <input
                  type="password"
                  data-testid="delete-password-input"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  autoComplete="current-password"
                  className="mt-1 block w-64 rounded-xl border border-border-strong bg-surface px-3 py-2"
                />
                <span className="mt-1 block text-sm text-ink-muted">
                  {t("privacy.passwordHelp")}
                </span>
              </label>
            ) : null}
            <button
              type="button"
              data-testid="delete-account-button"
              disabled={!canDelete || deleteAccountMutation.isPending}
              onClick={() => deleteAccountMutation.mutate()}
              className="mt-3 rounded-xl bg-danger-bg px-4 py-2 text-sm font-semibold text-danger-fg disabled:opacity-60"
            >
              {deleteAccountMutation.isPending ? t("privacy.deleting") : t("privacy.deleteButton")}
            </button>
          </>
        )}
      </div>

      {message !== null ? (
        <p role="status" className="mt-4 rounded-xl bg-accent-soft p-4">
          {message}
        </p>
      ) : null}
    </section>
  );
}

/** Curated copy per backend code: never the raw message, never a stack trace. */
function deletionErrorMessage(code: string, message: string): string {
  switch (code) {
    case "confirmation_required":
      return t("privacy.errConfirmationRequired");
    case "reauth_required":
      return t("privacy.errReauthRequired");
    case "invalid_credentials":
      return t("privacy.errInvalidCredentials");
    case "account_locked":
      return t("privacy.errAccountLocked");
    default:
      return t("privacy.errDeleteGeneric", { message });
  }
}

/** Hand a blob to the browser as a download, and release the object URL after. */
function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  try {
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    anchor.rel = "noopener";
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
  } finally {
    // Revoke on the next tick: some browsers need the URL alive during the click.
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
  }
}
