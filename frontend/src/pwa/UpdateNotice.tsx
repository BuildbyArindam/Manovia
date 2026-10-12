/**
 * "A new version is ready" — offered, never forced (Day 25).
 *
 * The service worker downloads updates in the background. Activating one
 * reloads the page, so it waits for the reader: "Reload now" or "Later". No
 * countdown, no auto-reload — a timed reload is a time limit (WCAG 2.2.1) and
 * would throw away a half-written note.
 */

import type { ReactElement } from "react";

import { t } from "@/lib/i18n/i18n";

import { applyUpdate, dismissUpdate, usePwaState } from "./registerServiceWorker";

export function UpdateNotice(): ReactElement | null {
  const { needRefresh } = usePwaState();
  if (!needRefresh) {
    return null;
  }
  return (
    <div
      role="status"
      data-print="hide"
      data-testid="update-notice"
      className="border-b border-border bg-accent-soft"
    >
      <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-3 px-4 py-3">
        <p className="text-sm text-ink">{t("pwa.updateReady")}</p>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => {
              void applyUpdate();
            }}
            className="min-h-11 rounded-xl bg-accent-bg px-4 text-sm font-semibold text-accent-fg"
          >
            {t("pwa.updateReload")}
          </button>
          <button
            type="button"
            onClick={dismissUpdate}
            className="min-h-11 rounded-xl border border-border-strong px-4 text-sm font-semibold text-ink"
          >
            {t("pwa.updateLater")}
          </button>
        </div>
      </div>
    </div>
  );
}
