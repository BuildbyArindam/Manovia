import { Link } from "react-router-dom";

import type { ReactElement } from "react";

import { CrisisHelpButton } from "@/components/CrisisHelpButton";
import { OverwhelmedButton } from "@/features/sensory/OverwhelmedButton";
import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import { t } from "@/lib/i18n/i18n";

/**
 * The app header. Present on every route, and carrying the controls that matter
 * at a glance: "I'm overwhelmed", the language of the whole interface, and
 * "Need help now?", which is never more than one glance away.
 *
 * Day 46 (CI fix): with three controls the row no longer fits a 320 px screen,
 * and `min-w-0` on the brand let it shrink to nothing while its link kept its
 * own width and painted over the controls beside it — which is what the Day-25
 * focus check caught as "a 'Manovia' under span.sm:hidden". Nothing here
 * shrinks below its content now: the row wraps, the controls take their own
 * line when they must, and they stay in source order, so no control can ever be
 * laid on top of another.
 */
export function Header(): ReactElement {
  return (
    <header
      data-print="hide"
      className="app-header sticky top-0 z-30 border-b border-border bg-surface"
    >
      <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-3 gap-y-2 px-4 py-2.5">
        <span className="flex shrink-0 items-center gap-2">
          <Link
            to="/"
            className="inline-flex min-h-11 items-center rounded-lg font-serif text-lg font-semibold tracking-tight text-ink"
          >
            {t("app.name")}
          </Link>
          {/* Day 30 positioning: the demo is visibly pre-release until the
              clinical review in docs/clinical-review.md is signed off. */}
          <span className="rounded-full border border-border bg-bg px-2 py-0.5 text-xs font-semibold uppercase tracking-wide text-ink-muted">
            {t("app.betaLabel")}
          </span>
        </span>
        <div className="ml-auto flex shrink-0 flex-wrap items-center justify-end gap-2 sm:gap-4">
          <OverwhelmedButton />
          <LanguageSwitcher />
          <CrisisHelpButton openFromUrl />
        </div>
      </div>
    </header>
  );
}
