/**
 * "You're offline" — said once, plainly, with what still works (Day 25).
 *
 * The banner sits in the page flow under the header rather than floating over
 * content: at 400% zoom a fixed banner would cover most of the screen and hide
 * whatever has focus (WCAG 2.4.11). The live region is always mounted, so a
 * screen reader hears the change when the connection drops *and* when it comes
 * back; the visible part only exists while offline, so nothing lingers on
 * screen once it is no longer true.
 */

import { useEffect, useRef, useState, type ReactElement } from "react";

import { t } from "@/lib/i18n/i18n";

import { useOnlineStatus } from "./useOnlineStatus";

export function OfflineBanner(): ReactElement {
  const online = useOnlineStatus();
  const wasOffline = useRef(!online);
  const [announceBack, setAnnounceBack] = useState(false);

  useEffect(() => {
    if (!online) {
      wasOffline.current = true;
      setAnnounceBack(false);
    } else if (wasOffline.current) {
      wasOffline.current = false;
      setAnnounceBack(true);
    }
  }, [online]);

  return (
    <div role="status" aria-live="polite" data-print="hide" data-testid="offline-region">
      {online ? (
        announceBack ? (
          <span className="sr-only" data-testid="back-online">
            {t("pwa.backOnline")}
          </span>
        ) : null
      ) : (
        <div
          data-testid="offline-banner"
          className="border-b border-border-strong bg-surface-muted"
        >
          <div className="mx-auto flex max-w-6xl items-start gap-3 px-4 py-3">
            <span
              aria-hidden="true"
              className="mt-2 inline-block h-2.5 w-2.5 shrink-0 rounded-full border-2 border-ink"
            />
            <p className="text-sm text-ink">
              <strong className="font-semibold">{t("pwa.offlineTitle")}</strong>{" "}
              {t("pwa.offlineBody")}
            </p>
          </div>
        </div>
      )}
    </div>
  );
}
