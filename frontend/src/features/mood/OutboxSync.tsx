/**
 * Sends queued check-ins when the connection returns (Day 25).
 *
 * Mounted once, in the app shell, so it runs whichever page is open. It syncs:
 *
 * * when the browser reports it is back online;
 * * when the app starts (or the tab becomes visible) with something waiting;
 * * every minute while something is still waiting and the device says it is
 *   online — the server may have been briefly unavailable (a `503` from the
 *   safety engine is retried, never skipped: a note is never stored unscreened).
 *
 * There is no background sync in the service worker on purpose: the worker
 * never holds the session, so it cannot send anything as anybody. A check-in
 * waits until Manovia is next open and online, and the copy says exactly that.
 *
 * When a synced check-in comes back with crisis help — the note read as high
 * risk once the server could screen it — the help is shown, because the reason
 * it was not shown at the time is only that the phone had no signal.
 */

import { useCallback, useEffect, useRef, useState, type ReactElement } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { CrisisCard } from "@/components/CrisisCard";
import { api, type ApiClient } from "@/lib/api";
import { t } from "@/lib/i18n/i18n";
import { useOnboarding } from "@/onboarding/OnboardingProvider";
import { useOnlineStatus } from "@/pwa/useOnlineStatus";

import { syncOutbox } from "./outbox";
import type { MoodCrisis } from "./types";
import { moodKeys, useMoodOutbox } from "./useMood";

/** How often to retry while something is waiting and the device is online. */
export const OUTBOX_RETRY_MS = 60_000;

export interface OutboxSyncProps {
  client?: ApiClient;
}

export function OutboxSync({ client = api }: OutboxSyncProps): ReactElement {
  const online = useOnlineStatus();
  const queued = useMoodOutbox();
  const pending = queued.filter((item) => item.status === "pending").length;
  const { record } = useOnboarding();
  const userId = record?.user.id ?? null;
  const queryClient = useQueryClient();
  const [announcement, setAnnouncement] = useState("");
  const [crisis, setCrisis] = useState<MoodCrisis | null>(null);
  const running = useRef(false);

  const run = useCallback(async (): Promise<void> => {
    if (running.current || record === null) {
      return;
    }
    running.current = true;
    try {
      const result = await syncOutbox({ client, userId });
      if (result.sent.length > 0) {
        setAnnouncement(
          result.sent.length === 1
            ? t("pwa.syncedOne")
            : t("pwa.syncedMany", { count: result.sent.length }),
        );
        const help = result.sent.find((sent) => sent.response.crisis !== null)?.response.crisis;
        if (help !== undefined && help !== null) {
          setCrisis(help);
        }
        await Promise.all([
          queryClient.invalidateQueries({ queryKey: moodKeys.history }),
          queryClient.invalidateQueries({ queryKey: moodKeys.streak }),
        ]);
      }
    } finally {
      running.current = false;
    }
  }, [client, queryClient, record, userId]);

  // Back online, or something new is waiting while online.
  useEffect(() => {
    if (online && pending > 0) {
      void run();
    }
  }, [online, pending, run]);

  // A slow, bounded retry while the server is unavailable.
  useEffect(() => {
    if (!online || pending === 0) {
      return undefined;
    }
    const timer = window.setInterval(() => {
      void run();
    }, OUTBOX_RETRY_MS);
    return () => {
      window.clearInterval(timer);
    };
  }, [online, pending, run]);

  // A tab brought back to the front is a good moment to try.
  useEffect(() => {
    const onVisible = (): void => {
      if (document.visibilityState === "visible" && navigator.onLine !== false) {
        void run();
      }
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [run]);

  return (
    <>
      <p className="sr-only" role="status" aria-live="polite" data-testid="outbox-announcer">
        {announcement}
      </p>
      {crisis !== null ? (
        <div className="mx-auto max-w-6xl px-4 pt-4" data-testid="synced-crisis" data-print="hide">
          <div className="rounded-2xl border-2 border-danger bg-surface p-5 shadow-soft">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <h2 className="text-lg">{t("pwa.syncedCrisisHeading")}</h2>
              <button
                type="button"
                onClick={() => {
                  setCrisis(null);
                }}
                className="min-h-11 rounded-xl border border-border-strong px-4 text-sm font-semibold"
              >
                {t("pwa.syncedCrisisClose")}
              </button>
            </div>
            <p className="mt-2 text-ink-muted">{t("pwa.syncedCrisisIntro")}</p>
            <div className="mt-4">
              <CrisisCard
                message={crisis.message}
                resources={crisis.resources}
                emergency={crisis.emergency}
                announce
                heading={null}
                testId="synced-crisis-card"
              />
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}
