import { Link } from "react-router-dom";

import type { ReactElement } from "react";

import { CrisisHelpButton } from "@/components/CrisisHelpButton";
import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import { t } from "@/lib/i18n/i18n";

/**
 * The app header. Present on every route, and carrying the two controls that
 * matter at a glance: the language of the whole interface, and "Need help
 * now?", which is never more than one glance away.
 */
export function Header(): ReactElement {
  return (
    <header data-print="hide" className="sticky top-0 z-30 border-b border-border bg-surface">
      <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-2 px-4 py-2.5 sm:gap-3 sm:py-3">
        <Link
          to="/"
          className="inline-flex min-h-11 items-center rounded-lg px-1 font-serif text-lg font-semibold tracking-tight text-ink"
        >
          {t("app.name")}
        </Link>
        <div className="flex flex-wrap items-center gap-2 sm:gap-4">
          <LanguageSwitcher />
          <CrisisHelpButton />
        </div>
      </div>
    </header>
  );
}
