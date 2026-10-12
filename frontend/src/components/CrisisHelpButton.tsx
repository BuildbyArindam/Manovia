import { useEffect, useState, type ReactElement } from "react";

import { CrisisResourcesModal } from "@/components/CrisisResourcesModal";
import { t } from "@/lib/i18n/i18n";

/**
 * The persistent "Need help now?" control.
 *
 * It lives in the header, so it is on every page without any page having to
 * remember it. It opens a modal dialog rather than navigating away: someone in
 * trouble should not lose their place, and a route change would also lose the
 * keyboard focus that makes the dialog usable.
 */
export function CrisisHelpButton({
  openFromUrl = false,
}: {
  /**
   * Day 25: the header's button also answers `?help=1`, the target of the
   * "Need help now?" shortcut on an installed app's icon — one long-press from
   * the home screen to the helplines. Only one instance may set this.
   */
  openFromUrl?: boolean;
} = {}): ReactElement {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!openFromUrl) {
      return;
    }
    const params = new URLSearchParams(window.location.search);
    if (params.get("help") === "1") {
      setOpen(true);
      // Leave the address clean, so a reload does not reopen the dialog.
      params.delete("help");
      const query = params.toString();
      window.history.replaceState(
        window.history.state,
        "",
        `${window.location.pathname}${query === "" ? "" : `?${query}`}${window.location.hash}`,
      );
    }
  }, [openFromUrl]);

  return (
    <>
      <button
        type="button"
        onClick={() => {
          setOpen(true);
        }}
        aria-haspopup="dialog"
        aria-expanded={open}
        className="rounded-xl border-2 border-danger bg-danger-soft px-3 py-2 text-sm font-semibold text-danger sm:px-4 sm:text-base"
      >
        {t("crisis.helpButton")}
      </button>
      {open ? <CrisisResourcesModal onClose={() => setOpen(false)} /> : null}
    </>
  );
}
