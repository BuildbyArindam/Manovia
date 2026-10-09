import { useState, type ReactElement } from "react";

import { CrisisResourcesModal } from "@/components/CrisisResourcesModal";

/**
 * The persistent "Need help now?" control.
 *
 * It lives in the header, so it is on every page without any page having to
 * remember it. It opens a modal dialog rather than navigating away: someone in
 * trouble should not lose their place, and a route change would also lose the
 * keyboard focus that makes the dialog usable.
 */
export function CrisisHelpButton(): ReactElement {
  const [open, setOpen] = useState(false);

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
        Need help now?
      </button>
      {open ? <CrisisResourcesModal onClose={() => setOpen(false)} /> : null}
    </>
  );
}
