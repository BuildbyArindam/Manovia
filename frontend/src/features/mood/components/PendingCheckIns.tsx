/**
 * Check-ins waiting on this device, said in one line (Day 25).
 *
 * Shown on the mood page only when something is queued. Waiting check-ins send
 * themselves (see `OutboxSync`); "Send now" is there for the person who would
 * rather not wait. A check-in that can never be sent as it is (older than the
 * 7-day window, made under another account) is not dropped silently — it is
 * listed with the reason and a "Discard" button, because deleting somebody's
 * words without telling them is worse than a line of explanation.
 */

import { useState, type ReactElement } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { api, type ApiClient } from "@/lib/api";
import { formatDateTime } from "@/lib/dates";
import { t } from "@/lib/i18n/i18n";
import { useOnboarding } from "@/onboarding/OnboardingProvider";
import { useOnlineStatus } from "@/pwa/useOnlineStatus";

import { removeFromOutbox, syncOutbox, type OutboxFailure } from "../outbox";
import { moodKeys, useMoodOutbox } from "../useMood";

function reason(failure: OutboxFailure | null): string {
  switch (failure) {
    case "too_old":
      return t("mood.failedTooOld");
    case "time_rejected":
      return t("mood.failedTime");
    case "account_changed":
      return t("mood.failedAccount");
    case "consent_required":
      return t("mood.failedConsent");
    case "session_expired":
      return t("mood.failedSession");
    default:
      return t("mood.failedRejected");
  }
}

export function PendingCheckIns({ client = api }: { client?: ApiClient }): ReactElement | null {
  const queued = useMoodOutbox();
  const online = useOnlineStatus();
  const { record } = useOnboarding();
  const queryClient = useQueryClient();
  const [sending, setSending] = useState(false);

  const pending = queued.filter((item) => item.status === "pending");
  const failed = queued.filter((item) => item.status === "failed");
  if (pending.length === 0 && failed.length === 0) {
    return null;
  }

  async function sendNow(): Promise<void> {
    setSending(true);
    try {
      const result = await syncOutbox({ client, userId: record?.user.id ?? null });
      if (result.sent.length > 0) {
        await Promise.all([
          queryClient.invalidateQueries({ queryKey: moodKeys.history }),
          queryClient.invalidateQueries({ queryKey: moodKeys.streak }),
        ]);
      }
    } finally {
      setSending(false);
    }
  }

  return (
    <section
      aria-labelledby="pending-check-ins-heading"
      data-testid="pending-check-ins"
      className="space-y-3 rounded-2xl border border-border-strong bg-surface p-5"
    >
      <h2 id="pending-check-ins-heading" className="text-base">
        {t("mood.pendingHeading")}
      </h2>
      {pending.length > 0 ? (
        <div className="flex flex-wrap items-center gap-3">
          <p data-testid="pending-count">
            {pending.length === 1
              ? t("mood.pendingOne")
              : t("mood.pendingMany", { count: pending.length })}{" "}
            {online ? null : <span className="text-ink-muted">{t("mood.pendingOffline")}</span>}
          </p>
          {online ? (
            <button
              type="button"
              onClick={() => {
                void sendNow();
              }}
              disabled={sending}
              className="min-h-11 rounded-xl border border-border-strong px-4 text-sm font-semibold disabled:opacity-60"
            >
              {sending ? t("mood.pendingSending") : t("mood.pendingSendNow")}
            </button>
          ) : null}
        </div>
      ) : null}
      {failed.length > 0 ? (
        <ul className="space-y-2" data-testid="failed-check-ins">
          {failed.map((item) => {
            const when = formatDateTime(item.recordedAt);
            return (
              <li
                key={item.clientId}
                className="flex flex-wrap items-center justify-between gap-3 rounded-xl bg-surface-muted p-3"
              >
                <p className="text-sm">
                  <span className="font-semibold">{t("mood.failedLine", { when })}</span>{" "}
                  {reason(item.failure)}
                </p>
                <button
                  type="button"
                  onClick={() => {
                    removeFromOutbox(item.clientId);
                  }}
                  aria-label={t("mood.failedDiscardLabel", { when })}
                  className="min-h-11 rounded-xl border border-border-strong px-4 text-sm font-semibold"
                >
                  {t("mood.failedDiscard")}
                </button>
              </li>
            );
          })}
        </ul>
      ) : null}
    </section>
  );
}
