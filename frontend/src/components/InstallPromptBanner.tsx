/**
 * Non-pushy PWA install prompt card (Day 25).
 *
 * Sits quietly above the footer when the browser offers installation and the
 * person has not dismissed it. Never opens a modal, never steals focus, and one
 * tap on "Not now" hides it permanently while leaving the install button
 * available in Settings.
 */

import type { ReactElement } from "react";

import { t } from "@/lib/i18n/i18n";
import { useInstallPrompt } from "@/lib/pwa/installPrompt";

export function InstallPromptBanner(): ReactElement | null {
  const { shouldShowBanner, promptInstall, dismiss } = useInstallPrompt();

  if (!shouldShowBanner) {
    return null;
  }

  return (
    <aside
      aria-label={t("pwa.installRegionLabel")}
      data-print="hide"
      data-testid="pwa-install-banner"
      className="border-t border-border bg-surface-muted px-4 py-3 text-sm text-ink"
    >
      <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3">
        <p className="text-ink-muted">{t("pwa.installBody")}</p>
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            data-testid="pwa-install-accept"
            onClick={() => {
              void promptInstall();
            }}
            className="min-h-11 rounded-xl bg-accent-bg px-4 py-2 text-xs font-semibold text-accent-fg"
          >
            {t("pwa.installAccept")}
          </button>
          <button
            type="button"
            data-testid="pwa-install-dismiss"
            onClick={dismiss}
            className="min-h-11 rounded-xl border border-border-strong bg-surface px-4 py-2 text-xs font-semibold text-ink"
          >
            {t("pwa.installDismiss")}
          </button>
        </div>
      </div>
    </aside>
  );
}
