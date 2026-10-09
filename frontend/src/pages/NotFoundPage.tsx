import { Link } from "react-router-dom";

import type { ReactElement } from "react";

/** 404 — calm, useful, and never a dead end. */
export function NotFoundPage(): ReactElement {
  return (
    <div className="rounded-2xl border border-border bg-surface p-6 shadow-soft sm:p-8">
      <h1 className="text-2xl sm:text-3xl">That page does not exist</h1>
      <p className="mt-3 text-lg text-ink-muted">
        The link may be old, or the page may have moved. Nothing is broken.
      </p>
      <Link
        to="/chat"
        className="mt-5 inline-block rounded-xl bg-accent-bg px-5 py-3 font-semibold text-accent-fg"
      >
        Back to chat
      </Link>
    </div>
  );
}
