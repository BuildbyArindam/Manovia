/**
 * Offline status & mood queue banner (Day 25).
 *
 * Rendered inside `AppShell` below the header so it is reachable on every page
 * without covering navigation or opening a dialog. Announces connectivity
 * changes politely (`role="status"`, `aria-live="polite"`), states clearly what
 * still works offline (helplines, breathing exercises, mood check-ins, local
 * journal drafts), and flushes queued mood check-ins automatically on reconnect.
 */

import { useEffect, useState, type ReactElement } from "react";

import { useOfflineMoodQueue } from "@/features/mood/useMood";
import type { ApiClient } from "@/lib/api";
import { t } from "@/lib/i18n/i18n";

export interface OfflineBannerProps {
  client?: ApiClient;
}

export function OfflineBanner({ client }: OfflineBannerProps): ReactElement | null {
  const [isOffline, setIsOffline] = useState<boolean>(
    () => typeof navigator !== "undefined" && navigator.onLine === false,
  );
  const { queuedCount, isSyncing, lastSyncedCount, syncNow } = useOfflineMoodQueue(client);

  useEffect(() => {
    const handleOnline = (): void => {
      setIsOffline(false);
    };
    const handleOffline = (): void => {
      setIsOffline(true);
    };
    window.addEventListener("online", handleOnline);
    window.addEventListener("offline", handleOffline);
    return () => {
      window.removeEventListener("online", handleOnline);
      window.removeEventListener("offline", handleOffline);
    };
  }, []);

  if (!isOffline && queuedCount === 0 && !isSyncing && lastSyncedCount === 0) {
    return null;
  }

  return (
    <div
      role="status"
      aria-live="polite"
      data-print="hide"
      data-testid={isOffline ? "offline-banner" : "offline-sync-banner"}
      className="border-b border-border-strong bg-surface-muted px-4 py-2.5 text-sm text-ink"
    >
      <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3">
        <div className="space-y-0.5">
          {isOffline ? (
            <>
              <p className="font-semibold" data-testid="offline-banner-heading">
                {t("pwa.offlineHeading")}
              </p>
              <p className="text-ink-muted">{t("pwa.offlineBody")}</p>
            </>
          ) : isSyncing ? (
            <p className="font-medium">{t("pwa.syncingMoodQueue")}</p>
          ) : queuedCount > 0 ? (
            <p className="font-medium">
              {t("pwa.pendingMoodQueue", { count: queuedCount })}
            </p>
          ) : (
            <p className="font-medium" data-testid="offline-sync-complete">
              {t("pwa.syncedMoodQueue", { count: lastSyncedCount })}
            </p>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-3">
          {queuedCount > 0 ? (
            <span
              data-testid="offline-queue-count"
              className="rounded-full border border-border-strong bg-surface px-3 py-1 text-xs font-semibold"
            >
              {t("pwa.queuedCountBadge", { count: queuedCount })}
            </span>
          ) : null}

          {!isOffline && queuedCount > 0 ? (
            <button
              type="button"
              data-testid="offline-sync-now"
              disabled={isSyncing}
              onClick={() => {
                void syncNow();
              }}
              className="min-h-11 rounded-xl border border-border-strong bg-surface px-4 py-2 text-xs font-semibold text-ink disabled:opacity-60"
            >
              {isSyncing ? t("pwa.syncingButton") : t("pwa.syncNowButton")}
            </button>
          ) : null}
        </div>
      </div>
    </div>
  );
}
