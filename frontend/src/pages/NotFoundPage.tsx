import { Link } from "react-router-dom";

import type { ReactElement } from "react";

import { t } from "@/lib/i18n/i18n";

/** 404 — calm, useful, and never a dead end. */
export function NotFoundPage(): ReactElement {
  return (
    <div className="rounded-2xl border border-border bg-surface p-6 shadow-soft sm:p-8">
      <h1 className="text-2xl sm:text-3xl">{t("notFound.heading")}</h1>
      <p className="mt-3 text-lg text-ink-muted">
        {t("notFound.body")}
      </p>
      <Link
        to="/chat"
        className="mt-5 inline-block rounded-xl bg-accent-bg px-5 py-3 font-semibold text-accent-fg"
      >
        {t("notFound.backToChat")}
      </Link>
    </div>
  );
}
