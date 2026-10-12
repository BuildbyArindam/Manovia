/**
 * Settings → "Install and offline use" (Day 25).
 *
 * The only place Manovia ever mentions installation. It explains, offers one
 * button when the browser allows it, remembers "Not now", and says plainly what
 * is kept on the device for offline use — and what is not.
 */

import { useEffect, useState, type ReactElement } from "react";

import { t } from "@/lib/i18n/i18n";

import { promptInstall, rememberInstallDismissed, useInstallAvailability } from "./installPrompt";
import { isControlledByServiceWorker, usePwaState } from "./registerServiceWorker";

function useOfflineReady(): boolean {
  const { offlineReady } = usePwaState();
  const [controlled, setControlled] = useState(isControlledByServiceWorker);

  useEffect(() => {
    if (typeof navigator === "undefined" || !("serviceWorker" in navigator)) {
      return undefined;
    }
    const update = (): void => setControlled(isControlledByServiceWorker());
    navigator.serviceWorker.addEventListener("controllerchange", update);
    return () => {
      navigator.serviceWorker.removeEventListener("controllerchange", update);
    };
  }, []);

  return offlineReady || controlled;
}

export function InstallSection(): ReactElement {
  const availability = useInstallAvailability();
  const offlineReady = useOfflineReady();

  return (
    <section
      aria-labelledby="install-heading"
      data-testid="install-section"
      className="rounded-2xl border border-border bg-surface p-6 shadow-soft"
    >
      <h2 id="install-heading" className="text-lg">
        {t("pwa.installHeading")}
      </h2>
      <p className="mt-2 text-ink-muted">{t("pwa.installIntro")}</p>

      <div className="mt-4" data-testid="install-state" data-availability={availability}>
        {availability === "installed" ? <p>{t("pwa.installInstalled")}</p> : null}
        {availability === "available" ? (
          <div className="flex flex-wrap gap-3">
            <button
              type="button"
              className="btn-primary"
              onClick={() => {
                // On "accepted" the browser fires `appinstalled`, which switches
                // this section to its installed state; "dismissed" is remembered.
                void promptInstall();
              }}
            >
              {t("pwa.installButton")}
            </button>
            <button
              type="button"
              className="btn-secondary"
              onClick={() => {
                rememberInstallDismissed();
              }}
            >
              {t("pwa.installNotNow")}
            </button>
          </div>
        ) : null}
        {availability === "dismissed" ? (
          <p className="text-ink-muted">{t("pwa.installDismissed")}</p>
        ) : null}
        {availability === "ios" ? <p>{t("pwa.installIos")}</p> : null}
        {availability === "unsupported" ? (
          <p className="text-ink-muted">{t("pwa.installUnsupported")}</p>
        ) : null}
      </div>

      <p className="mt-4 font-semibold" data-testid="offline-ready">
        {offlineReady ? t("pwa.offlineReady") : t("pwa.offlineNotReady")}
      </p>
      <p className="mt-1 text-sm text-ink-muted" data-testid="offline-data-note">
        {t("pwa.offlineDataNote")}
      </p>
    </section>
  );
}
