/**
 * The opt-in, three-day Mood Outlook card.
 *
 * The server owns every sentence. This component renders the numeric personal
 * range as a semantic table, so the uncertainty is available without reading a
 * chart, relying on colour, or interpreting a visual band.
 */

import type { ReactElement } from "react";
import { Link } from "react-router-dom";

import { formatLocalDay } from "@/lib/dates";
import { isApiError, type ApiClient } from "@/lib/api";
import type { AppLanguage } from "@/lib/i18n/i18n";

import { INSIGHTS_COPY } from "../copy";
import type { MoodOutlook } from "../types";
import { useMoodOutlook } from "../useInsights";

const CARD_CLASS = "rounded-2xl border border-border bg-surface p-6 shadow-soft";

export interface MoodOutlookCardProps {
  client?: ApiClient;
  locale: AppLanguage;
}

export function MoodOutlookCard({ client, locale }: MoodOutlookCardProps): ReactElement {
  const { settingsQuery, outlookQuery } = useMoodOutlook(client, { locale });

  if (settingsQuery.isPending) {
    return (
      <section
        aria-labelledby="mood-outlook-heading"
        className={CARD_CLASS}
        data-testid="mood-outlook"
      >
        <h2 id="mood-outlook-heading" className="text-lg">
          {INSIGHTS_COPY.outlookTitle}
        </h2>
        <p className="mt-3 text-sm text-ink-muted" role="status">
          {INSIGHTS_COPY.outlookCheckingSetting}
        </p>
      </section>
    );
  }

  if (settingsQuery.isError) {
    return (
      <section
        aria-labelledby="mood-outlook-heading"
        className={CARD_CLASS}
        data-testid="mood-outlook"
      >
        <h2 id="mood-outlook-heading" className="text-lg">
          {INSIGHTS_COPY.outlookTitle}
        </h2>
        <p className="mt-3 max-w-prose text-sm text-ink-muted" role="alert">
          {INSIGHTS_COPY.outlookSettingsError}
          {isApiError(settingsQuery.error) && settingsQuery.error.code === "consent_required"
            ? ` ${INSIGHTS_COPY.consentBody}`
            : ""}
        </p>
        <Link
          to="/settings"
          className="mt-4 inline-flex min-h-11 items-center rounded-xl border border-border-strong px-4 py-2 font-semibold underline underline-offset-4"
        >
          {INSIGHTS_COPY.outlookOpenSettings}
        </Link>
      </section>
    );
  }

  if (settingsQuery.data.mood_outlook_enabled !== true) {
    return (
      <section
        aria-labelledby="mood-outlook-heading"
        className={CARD_CLASS}
        data-testid="mood-outlook"
      >
        <h2 id="mood-outlook-heading" className="text-lg">
          {INSIGHTS_COPY.outlookTitle}
        </h2>
        <p className="mt-2 text-sm text-ink-muted">{INSIGHTS_COPY.outlookIntro}</p>
        <p className="mt-3 max-w-prose text-ink-muted">{INSIGHTS_COPY.outlookOptInBody}</p>
        <Link
          to="/settings"
          className="mt-4 inline-flex min-h-11 items-center rounded-xl border border-border-strong px-4 py-2 font-semibold underline underline-offset-4"
        >
          {INSIGHTS_COPY.outlookOpenSettings}
        </Link>
      </section>
    );
  }

  if (outlookQuery.isPending) {
    return (
      <section
        aria-labelledby="mood-outlook-heading"
        className={CARD_CLASS}
        data-testid="mood-outlook"
      >
        <h2 id="mood-outlook-heading" className="text-lg">
          {INSIGHTS_COPY.outlookTitle}
        </h2>
        <p className="mt-2 text-sm text-ink-muted">{INSIGHTS_COPY.outlookIntro}</p>
        <p className="mt-3 text-sm text-ink-muted" role="status">
          {INSIGHTS_COPY.outlookLoading}
        </p>
      </section>
    );
  }

  if (outlookQuery.isError) {
    return (
      <section
        aria-labelledby="mood-outlook-heading"
        className={CARD_CLASS}
        data-testid="mood-outlook"
      >
        <h2 id="mood-outlook-heading" className="text-lg">
          {INSIGHTS_COPY.outlookTitle}
        </h2>
        <p className="mt-2 text-sm text-ink-muted">{INSIGHTS_COPY.outlookIntro}</p>
        <p className="mt-3 text-ink-muted" role="alert">
          {INSIGHTS_COPY.outlookFetchError}
        </p>
        <button
          type="button"
          onClick={() => void outlookQuery.refetch()}
          className="mt-4 min-h-11 rounded-xl border border-border-strong px-4 py-2 font-semibold focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
        >
          {INSIGHTS_COPY.outlookRetry}
        </button>
      </section>
    );
  }

  return <ReadyOutlook data={outlookQuery.data} locale={locale} />;
}

function ReadyOutlook({ data, locale }: { data: MoodOutlook; locale: AppLanguage }): ReactElement {
  const number = new Intl.NumberFormat(locale, { maximumFractionDigits: 1 });
  const displayNumber = (value: number): string => number.format(value);

  return (
    <section
      aria-labelledby="mood-outlook-heading"
      aria-describedby="mood-outlook-uncertainty mood-outlook-disclaimer"
      className={CARD_CLASS}
      data-testid="mood-outlook"
    >
      <h2 id="mood-outlook-heading" className="text-lg">
        {INSIGHTS_COPY.outlookTitle}
      </h2>
      <p className="mt-2 text-sm text-ink-muted">{INSIGHTS_COPY.outlookIntro}</p>
      <p className="mt-3 max-w-prose">{data.summary}</p>
      <p id="mood-outlook-uncertainty" className="mt-2 max-w-prose text-sm text-ink-muted">
        {data.uncertainty_note}
      </p>
      <p id="mood-outlook-disclaimer" className="mt-2 max-w-prose text-sm text-ink-muted">
        {data.not_health_advice}
      </p>

      {data.available && data.forecast.length > 0 ? (
        <div className="mt-5">
          <h3 className="font-semibold">{INSIGHTS_COPY.outlookForecastHeading}</h3>
          <div className="mt-2 overflow-x-auto rounded-xl border border-border">
            <table className="w-full min-w-[30rem] text-left text-sm">
              <caption className="sr-only">{INSIGHTS_COPY.outlookTableCaption}</caption>
              <thead className="bg-surface-muted">
                <tr>
                  <th scope="col" className="px-3 py-2 font-semibold">
                    {INSIGHTS_COPY.outlookDay}
                  </th>
                  <th scope="col" className="px-3 py-2 font-semibold">
                    {INSIGHTS_COPY.outlookEstimate}
                  </th>
                  <th scope="col" className="px-3 py-2 font-semibold">
                    {INSIGHTS_COPY.outlookPossibleRange}
                  </th>
                </tr>
              </thead>
              <tbody>
                {data.forecast.map((point) => {
                  const lower = displayNumber(point.lower);
                  const upper = displayNumber(point.upper);
                  return (
                    <tr key={point.day} className="border-t border-border">
                      <th scope="row" className="px-3 py-2 font-medium">
                        <time dateTime={point.day}>{formatLocalDay(point.day)}</time>
                      </th>
                      <td className="px-3 py-2">{displayNumber(point.estimate)}</td>
                      <td className="px-3 py-2">
                        <span aria-label={INSIGHTS_COPY.outlookRangeValue(lower, upper)}>
                          {lower}–{upper}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}

      {data.top_factors.length > 0 ? (
        <section aria-labelledby="mood-outlook-factors-heading" className="mt-5">
          <h3 id="mood-outlook-factors-heading" className="font-semibold">
            {INSIGHTS_COPY.outlookFactorsHeading}
          </h3>
          <ul className="mt-2 max-w-prose list-disc space-y-2 pl-5">
            {data.top_factors.map((factor) => (
              <li key={factor.tag}>{factor.sentence}</li>
            ))}
          </ul>
        </section>
      ) : data.no_factors_note !== null ? (
        <p className="mt-4 max-w-prose text-sm text-ink-muted">{data.no_factors_note}</p>
      ) : null}

      {data.small_action !== null ? (
        <div className="mt-5 rounded-xl bg-surface-muted p-4">
          <h3 className="font-semibold">{INSIGHTS_COPY.outlookSmallActionHeading}</h3>
          <p className="mt-1 max-w-prose">{data.small_action}</p>
        </div>
      ) : null}
      {data.optional_support !== null ? (
        <div className="mt-4 rounded-xl border border-border-strong p-4">
          <h3 className="font-semibold">{INSIGHTS_COPY.outlookSupportHeading}</h3>
          <p className="mt-1 max-w-prose text-ink-muted">{data.optional_support}</p>
        </div>
      ) : null}

      {data.timezone_fallback_used ? (
        <p className="mt-3 text-sm text-ink-muted">{INSIGHTS_COPY.outlookTimezoneFallback}</p>
      ) : null}
      {data.locale_fallback_used ? (
        <p className="mt-1 text-sm text-ink-muted">
          {INSIGHTS_COPY.outlookLocaleFallback(data.locale)}
        </p>
      ) : null}
    </section>
  );
}
