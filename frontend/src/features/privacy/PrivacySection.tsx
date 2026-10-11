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

const CONSENT_LABELS: Record<string, string> = {
  terms: "Terms of use",
  privacy: "Privacy notice",
  ai_disclosure: "About the AI",
  store_chat: "Saving chat history",
};

/** Plain-language: what is stored, where it lives, and how it is protected. */
const STORAGE_FACTS: ReadonlyArray<{ title: string; detail: string }> = [
  {
    title: "On this device only",
    detail:
      "Your palette, whether the mood hint is shown, and your sign-in tokens stay in this browser. They are never sent anywhere as preferences.",
  },
  {
    title: "On Manovia's server, encrypted",
    detail:
      "Journal entries, the messages of chats you chose to save, and any note you attach to a check-in. Each is encrypted with a key that belongs to your account, so it is unreadable at rest without you.",
  },
  {
    title: "On Manovia's server, as numbers",
    detail:
      "Check-in scales (how you rated mood and energy), which tags you picked, and thought-record ratings. These are what make trends possible.",
  },
  {
    title: "Safety events: never your words",
    detail:
      "When a screening answer is high risk, Manovia records the level, the source and a category — no message text, no journal text, no note. That is why you can delete everything above and the safety record still contains nothing you wrote.",
  },
];

function describeKeptFor(days: number | null): string {
  if (days === null) {
    return "Keep everything until I delete it.";
  }
  if (days === 30) {
    return "Delete anything older than 30 days.";
  }
  if (days === 90) {
    return "Delete anything older than 3 months.";
  }
  if (days === 180) {
    return "Delete anything older than 6 months.";
  }
  if (days === 365) {
    return "Delete anything older than 1 year.";
  }
  return `Delete anything older than ${days} days.`;
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
      setMessage("Saved. This is what Manovia will do from now on.");
      queryClient.setQueryData(privacyKeys.settings, saved);
      await queryClient.invalidateQueries({ queryKey: privacyKeys.settings });
    },
    onError: (error) => {
      setMessage(
        isApiError(error)
          ? `That choice could not be saved (${error.code}). Please try again.`
          : "That choice could not be saved right now. Please try again.",
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
      setMessage(
        `${filename} has been prepared and your browser is downloading it. It is decrypted so you can read it — keep it somewhere safe.`,
      );
    },
    onError: (error) => {
      setMessage(
        isApiError(error)
          ? `The export could not be prepared (${error.code}). Nothing was downloaded.`
          : "The export could not be prepared right now. Nothing was downloaded.",
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
      setMessage(
        "Your account and its data have been deleted. The receipt below is all that remains.",
      );
      client.clearSession();
      reset();
      onDeleted?.(result);
    },
    onError: (error) => {
      if (isApiError(error)) {
        setMessage(deletionErrorMessage(error.code, error.message));
        return;
      }
      setMessage("Deletion could not be completed. Nothing was deleted.");
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
        Privacy
      </h2>
      <p className="mt-2 text-sm text-ink-muted">
        What Manovia keeps, what it does not, and how to take it or remove it. Nothing on this page
        needs a reason or an explanation.
      </p>

      <div className="mt-4 space-y-3">
        {STORAGE_FACTS.map((fact) => (
          <div key={fact.title} className="rounded-xl bg-surface-muted p-4">
            <p className="font-semibold">{fact.title}</p>
            <p className="mt-1 text-sm text-ink-muted">{fact.detail}</p>
          </div>
        ))}
      </div>

      {settingsQuery.isLoading ? (
        <p className="mt-5 text-sm text-ink-muted" role="status">
          Loading your privacy choices…
        </p>
      ) : settingsQuery.isError || settings === undefined ? (
        <p className="mt-5 text-sm text-ink-muted" role="alert">
          Your privacy choices could not be loaded right now. Everything else on this page still
          works.
        </p>
      ) : (
        <fieldset className="mt-5 space-y-4" disabled={save.isPending}>
          <legend className="font-semibold">Your choices</legend>

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
              <span className="block font-semibold">Let me save chats</span>
              <span className="block text-sm text-ink-muted">
                Off means saved chats stop being written and existing saved chats stop gaining new
                turns. You can still talk to Manovia — those conversations are simply not kept.
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
              <span className="block font-semibold">Keep mood scores from my writing</span>
              <span className="block text-sm text-ink-muted">
                These are guesses made by a language model, not measurements. Off means new journal
                entries store no score. Your words are still saved as you wrote them.
              </span>
            </span>
          </label>

          <label className="block">
            <span className="font-semibold">How long to keep things</span>
            <select
              data-testid="retention-select"
              value={settings.retention_days === null ? "forever" : String(settings.retention_days)}
              onChange={(event) => {
                const value = event.target.value;
                save.mutate({ retention_days: value === "forever" ? null : Number(value) });
              }}
              className="mt-2 block rounded-xl border border-border-strong bg-surface px-3 py-2"
            >
              <option value="forever">Keep everything until I delete it</option>
              {settings.retention_choices.map((days) => (
                <option key={days} value={String(days)}>
                  {describeKeptFor(days)}
                </option>
              ))}
            </select>
            <span className="mt-2 block text-sm text-ink-muted">
              A cleanup job runs against this window and deletes older entries. Deleting removes
              them; it does not anonymise them.{" "}
              {settings.been_saved
                ? ""
                : "Nothing here has been saved yet, so the defaults are showing."}
            </span>
          </label>
        </fieldset>
      )}

      <div className="mt-6 border-t border-border pt-5">
        <h3 className="font-semibold">Take your data with you</h3>
        <p className="mt-1 text-sm text-ink-muted">
          One file, everything Manovia holds about you, decrypted so you can read it: profile,
          agreements, saved chats, journal, check-ins, assessments, exercises, thought records, plan
          and safety events. Keep it somewhere safe — it is unencrypted once downloaded.
        </p>
        <button
          type="button"
          data-testid="export-button"
          disabled={!settingsQuery.isSuccess || exportData.isPending}
          onClick={() => exportData.mutate()}
          className="mt-3 rounded-xl bg-accent-bg px-4 py-2 text-sm font-semibold text-accent-fg disabled:opacity-60"
        >
          {exportData.isPending ? "Preparing your file…" : "Download my data"}
        </button>
      </div>

      <div className="mt-6 border-t border-border pt-5">
        <h3 className="font-semibold">What you have agreed to</h3>
        {consentsQuery.isLoading ? (
          <p className="mt-2 text-sm text-ink-muted" role="status">
            Loading your agreements…
          </p>
        ) : consentsQuery.isError ? (
          <p className="mt-2 text-sm text-ink-muted" role="alert">
            Your agreement history could not be loaded right now.
          </p>
        ) : (consentsQuery.data?.items.length ?? 0) === 0 ? (
          <p className="mt-2 text-sm text-ink-muted">No agreements recorded on this account yet.</p>
        ) : (
          <ul className="mt-3 space-y-2" data-testid="consent-history">
            {consentsQuery.data?.items.map((item) => (
              <li
                key={`${item.kind}-${item.version}-${item.created_at}`}
                className="flex flex-wrap items-baseline justify-between gap-2 rounded-xl bg-surface-muted px-4 py-3"
              >
                <span className="font-medium">{CONSENT_LABELS[item.kind] ?? item.kind}</span>
                <span className="text-sm text-ink-muted">
                  {item.granted ? "agreed" : "withdrawn"} · version {item.version} ·{" "}
                  {formatDateTime(item.created_at)}
                </span>
              </li>
            ))}
          </ul>
        )}
        <p className="mt-2 text-sm text-ink-muted">
          Withdrawing an agreement is not offered here because it does not work like a switch:
          asking Manovia to stop using your data is what deletion below does. Everything else stays
          available to you either way.
        </p>
      </div>

      <div className="mt-6 border-t border-border pt-5">
        <h3 className="font-semibold">Anonymous mode</h3>
        <p className="mt-2 text-sm text-ink-muted" data-testid="anonymous-mode-copy">
          {record === null
            ? "Anonymous accounts carry no email and no password, so nothing here points back at a person. Everything else works exactly the same."
            : record.user.isAnonymous
              ? "You are using an anonymous account: no email, no password, nothing that identifies you. Everything else works the same — the data belongs to this device. The trade-off is that nobody, including you, can recover it if this browser forgets its tokens, so keep a copy of anything you want to keep."
              : "You signed up with an email address, which is the only thing that identifies you. If you would rather not be identifiable, you can delete this account and start again anonymously: everything works the same, and the data then belongs to the device alone."}
        </p>
        <p className="mt-2 text-sm text-ink-muted">
          Anonymous does not mean invisible: your language, your region and the content you choose
          to save are still stored as described above, and safety screens still run. It means
          Manovia holds no name, no email and no phone number that could point back at you.
        </p>
      </div>

      <div className="mt-6 border-t border-border pt-5">
        <h3 className="font-semibold">Delete everything</h3>
        <p className="mt-2 text-sm text-ink-muted">
          This deletes your account and every row attached to it, in one transaction. It cannot be
          undone, and it also ends this session — you would come back to a fresh, empty account.
        </p>
        {receipt !== null ? (
          <div
            role="status"
            data-testid="deletion-receipt"
            className="mt-3 rounded-xl border border-border-strong bg-surface-muted p-4"
          >
            <p className="font-semibold">Deleted. Here is your receipt.</p>
            <p className="mt-1 text-sm text-ink-muted">
              Reference {receipt.account_ref} · {receipt.total_rows} rows removed ·{" "}
              {formatDateTime(receipt.created_at)}
            </p>
            <ul className="mt-2 text-sm text-ink-muted">
              {Object.entries(receipt.rows_deleted)
                .filter(([, count]) => count > 0)
                .map(([table, count]) => (
                  <li key={table}>
                    {count} × {table}
                  </li>
                ))}
            </ul>
            <p className="mt-2 text-sm text-ink-muted">
              The reference is a one-way hash, not your account number: it proves the deletion
              happened without recording who you are.
            </p>
          </div>
        ) : (
          <>
            <label className="mt-3 block">
              <span className="text-sm font-semibold">Type {DELETE_CONFIRMATION} to confirm</span>
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
                <span className="text-sm font-semibold">Your password</span>
                <input
                  type="password"
                  data-testid="delete-password-input"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  autoComplete="current-password"
                  className="mt-1 block w-64 rounded-xl border border-border-strong bg-surface px-3 py-2"
                />
                <span className="mt-1 block text-sm text-ink-muted">
                  Required because this account signs in with a password.
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
              {deleteAccountMutation.isPending ? "Deleting…" : "Delete my account and data"}
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
      return "The confirmation phrase did not match. Nothing was deleted.";
    case "reauth_required":
      return "Enter your password to confirm. Nothing was deleted.";
    case "invalid_credentials":
      return "That password was not right. Nothing was deleted.";
    case "account_locked":
      return "Too many wrong passwords, so deletion is paused for a while. Nothing was deleted.";
    default:
      return `Deletion failed: ${message} Nothing was deleted.`;
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
