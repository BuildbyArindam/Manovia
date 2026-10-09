import { Link } from "react-router-dom";

import type { ReactElement } from "react";

import { CrisisHelpButton } from "@/components/CrisisHelpButton";

/**
 * The app header. Present on every route, and carrying the one control that must
 * never be more than one glance away: "Need help now?".
 */
export function Header(): ReactElement {
  return (
    <header className="sticky top-0 z-30 border-b border-border bg-surface">
      <div className="mx-auto flex max-w-6xl items-center justify-between gap-3 px-4 py-3">
        <Link
          to="/chat"
          className="rounded-lg font-serif text-lg font-semibold tracking-tight text-ink"
        >
          Manovia
        </Link>
        <CrisisHelpButton />
      </div>
    </header>
  );
}
