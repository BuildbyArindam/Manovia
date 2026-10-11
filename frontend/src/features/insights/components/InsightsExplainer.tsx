/**
 * The "About these insights" explainer.
 *
 * Everything in it is the server's copy, in the reader's language, from the same
 * reviewed template files that produced the sentences — including the limitations
 * and the methods. That matters more than it looks: a limitation list written on
 * the client is a limitation list that drifts from the rules the server actually
 * applies, and the day it does, the page is describing arithmetic it is not
 * doing.
 *
 * It is rendered on every visit, not behind a link in a footer. A person is about
 * be told something about themselves derived from statistics; the conditions of
 * that telling belong on the same screen.
 */

import type { ReactElement } from "react";

import type { AboutInsights } from "../types";

export interface InsightsExplainerProps {
  about: AboutInsights;
}

export function InsightsExplainer({ about }: InsightsExplainerProps): ReactElement {
  return (
    <section
      aria-labelledby="insights-about-heading"
      data-testid="insights-about"
      className="rounded-2xl border border-border bg-surface p-6 shadow-soft"
    >
      <h2 id="insights-about-heading" className="text-xl">
        {about.title}
      </h2>

      <p className="mt-3 max-w-prose" data-testid="insights-not-causal">
        {about.not_causal}
      </p>

      <div className="mt-5 grid gap-5 sm:grid-cols-2">
        <div>
          <h3 className="text-base">How these numbers are made</h3>
          <ul className="mt-2 list-disc space-y-2 pl-5 text-sm text-ink-muted">
            {about.methods.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
        <div>
          <h3 className="text-base">What this cannot tell you</h3>
          <ul className="mt-2 list-disc space-y-2 pl-5 text-sm text-ink-muted">
            {about.limitations.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
      </div>

      <p className="mt-5 rounded-xl border border-border bg-surface-muted p-4 text-sm text-ink-muted">
        {about.disclaimer}
      </p>

      {about.locale_fallback_used ? (
        <p className="mt-3 text-sm text-ink-muted" data-testid="insights-locale-fallback">
          Shown in {about.locale}.
        </p>
      ) : null}
    </section>
  );
}
