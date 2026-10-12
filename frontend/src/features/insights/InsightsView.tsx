/**
 * Insights — the page (rendered by `pages/InsightsPage.tsx`).
 *
 * The layout follows the order of trust. The server's own sentences come first,
 * in the reader's language, before any picture: somebody who reads only the top
 * of this page has been told everything the app is willing to claim. Then the
 * panels, each one a heading, sentences, a chart, and a disclosure with the same
 * numbers as a table. Then the explainer — the limitations, the methods, and the
 * line that says a difference between two sets of days is not a cause.
 *
 * Two things this page does not do, on purpose:
 *
 * - **It does not compose sentences about somebody's mood.** Every claim-shaped
 *   sentence on screen came from the server's reviewed templates. The only
 *   client-side wording is chrome (headings, buttons) and restatements of numbers
 *   the server already sent, which is why {@link INSIGHTS_COPY.rollingLine} takes
 *   the average as a string rather than deciding what to say about it.
 * - **It does not hide an empty state behind a spinner.** With too few check-ins
 *   the panels still render, with the server's "not enough data" sentence in
 *   them, because that sentence is the answer.
 */

import type { ReactElement } from "react";
import { Link } from "react-router-dom";
import type { UseQueryResult } from "@tanstack/react-query";

import { PageHeader } from "@/components/PageHeader";
import { isApiError, type ApiClient } from "@/lib/api";
import { useI18nState } from "@/lib/i18n/I18nProvider";
import { formatLocalDay } from "@/lib/dates";

import { BucketChart } from "./components/BucketChart";
import { DifferenceChart } from "./components/DifferenceChart";
import { InsightPanel } from "./components/InsightPanel";
import { InsightsExplainer } from "./components/InsightsExplainer";
import { DataTableView } from "./components/DataTableView";
import { RollingChart } from "./components/RollingChart";
import { WeeklyChart } from "./components/WeeklyChart";
import { INSIGHTS_COPY } from "./copy";
import {
  MEASURE_ENERGY,
  MEASURE_VALENCE,
  associationTable,
  bucketChartRows,
  bucketTable,
  differenceChartRows,
  formatPoints,
  rollingChartRows,
  rollingForMeasure,
  rollingTable,
  trendTable,
  weeklyChartRows,
  weeklyTable,
  type DataTable,
} from "./series";
import type { PatternInsights, Rolling, WeeklyInsights } from "./types";
import { usePatternInsights, useWeeklyInsights } from "./useInsights";

export interface InsightsViewProps {
  /** Injectable client: the tests drive this page against a fake transport. */
  client?: ApiClient;
}

export function InsightsView({ client }: InsightsViewProps): ReactElement {
  // Day 23: the insight sentences are written by the server; asking for them
  // in the interface language keeps them in step with everything else on
  // screen, and the language is part of the query key, so switching languages
  // re-reads them.
  const { language } = useI18nState();
  const patterns = usePatternInsights(client, { locale: language });
  const weekly = useWeeklyInsights(client, { locale: language });

  return (
    <div className="space-y-6" data-testid="insights-page">
      <PageHeader title={INSIGHTS_COPY.heading} lede={INSIGHTS_COPY.lede} />

      <PatternsSection query={patterns} />
      <WeeklySection query={weekly} />

      {patterns.data === undefined ? null : <InsightsExplainer about={patterns.data.about} />}
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Shared furniture                                                            //
// --------------------------------------------------------------------------- #

function StateNote({ children }: { children: string }): ReactElement {
  return (
    <p className="rounded-2xl border border-border bg-surface p-6 text-ink-muted shadow-soft">
      {children}
    </p>
  );
}

function ErrorPanel({ error, onRetry }: { error: Error; onRetry: () => void }): ReactElement {
  const consent = isApiError(error) && error.code === "consent_required";

  return (
    <section
      aria-labelledby="insights-error-heading"
      data-testid="insights-error"
      className="rounded-2xl border border-border bg-surface p-6 shadow-soft"
    >
      <h2 id="insights-error-heading" className="text-lg">
        {consent ? INSIGHTS_COPY.consentTitle : INSIGHTS_COPY.errorTitle}
      </h2>
      <p className="mt-2 max-w-prose text-ink-muted">
        {consent ? INSIGHTS_COPY.consentBody : INSIGHTS_COPY.errorBody}
      </p>
      {consent ? (
        <Link
          to="/settings"
          className="mt-4 inline-block rounded-lg bg-accent-bg px-4 py-2 text-sm text-accent-fg underline-offset-2 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
        >
          {INSIGHTS_COPY.consentAction}
        </Link>
      ) : (
        <button
          type="button"
          onClick={onRetry}
          className="mt-4 rounded-lg border border-border-strong px-4 py-2 text-sm hover:bg-surface-muted focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
        >
          {INSIGHTS_COPY.retry}
        </button>
      )}
    </section>
  );
}

/** The "based on N of your M check-ins" line, and the timezone caveat with it. */
function ProvenanceLine({ data }: { data: PatternInsights | WeeklyInsights }): ReactElement {
  return (
    <div className="space-y-1 text-sm text-ink-muted" data-testid="insights-provenance">
      <p>
        {INSIGHTS_COPY.generatedLine(
          data.considered_check_ins,
          data.total_check_ins,
          data.timezone,
        )}
      </p>
      {data.timezone_fallback_used ? <p>{INSIGHTS_COPY.timezoneFallback}</p> : null}
      {data.locale_fallback_used ? <p>{INSIGHTS_COPY.localeFallback(data.locale)}</p> : null}
    </div>
  );
}

/** The server's sentences, in reading order, ahead of everything else. */
function SummaryBlock({
  heading,
  sentences,
  testId,
}: {
  heading: string;
  sentences: readonly string[];
  testId: string;
}): ReactElement {
  return (
    <section
      aria-labelledby={`${testId}-heading`}
      data-testid={testId}
      className="rounded-2xl border border-border bg-surface p-6 shadow-soft"
    >
      <h2 id={`${testId}-heading`} className="text-lg">
        {heading}
      </h2>
      <div className="mt-3 max-w-prose space-y-2">
        {sentences.map((sentence) => (
          <p key={sentence}>{sentence}</p>
        ))}
      </div>
    </section>
  );
}

/** A table-only panel: numbers and their labels, no picture. */
function TablePanel({
  heading,
  summary,
  table,
  note,
  testId,
}: {
  heading: string;
  summary: readonly string[];
  table: DataTable;
  note?: string;
  testId: string;
}): ReactElement {
  return (
    <section
      aria-labelledby={`${testId}-heading`}
      data-testid={testId}
      className="rounded-2xl border border-border bg-surface p-6 shadow-soft"
    >
      <h3 id={`${testId}-heading`} className="text-lg">
        {heading}
      </h3>
      <div className="mt-3 max-w-prose space-y-2" data-testid="insight-summary">
        {summary.map((sentence) => (
          <p key={sentence}>{sentence}</p>
        ))}
      </div>
      <div className="mt-4">
        <DataTableView table={table} />
      </div>
      {note === undefined ? null : <p className="mt-3 text-sm text-ink-muted">{note}</p>}
    </section>
  );
}

// --------------------------------------------------------------------------- //
// Patterns                                                                    //
// --------------------------------------------------------------------------- #

function PatternsSection({
  query,
}: {
  query: UseQueryResult<PatternInsights, Error>;
}): ReactElement {
  if (query.isPending) {
    return <StateNote>{INSIGHTS_COPY.loading}</StateNote>;
  }
  if (query.isError) {
    return (
      <ErrorPanel
        error={query.error}
        onRetry={() => {
          void query.refetch();
        }}
      />
    );
  }

  const data = query.data;
  const today = data.today;

  const rollingPanels = [MEASURE_VALENCE, MEASURE_ENERGY].map((measure) => {
    const windows = rollingForMeasure(data.rolling, measure);
    const label = windows[0]?.label ?? measure;
    const [shortest, longest] = windows;
    return { measure, windows, label, shortest, longest };
  });

  const tagPanels = [
    { measure: MEASURE_VALENCE, heading: INSIGHTS_COPY.panelMoodTags, topic: "mood-and-tags" },
    { measure: MEASURE_ENERGY, heading: INSIGHTS_COPY.panelEnergyTags, topic: "energy-and-tags" },
  ];

  return (
    <>
      <SummaryBlock
        heading={INSIGHTS_COPY.sectionPatterns}
        sentences={data.summary}
        testId="insights-patterns-summary"
      />

      <ProvenanceLine data={data} />

      {!data.enough_data ? (
        <section
          aria-labelledby="insights-not-enough-heading"
          data-testid="insights-not-enough"
          className="rounded-2xl border border-border bg-surface-muted p-6"
        >
          <h2 id="insights-not-enough-heading" className="text-lg">
            {INSIGHTS_COPY.notEnoughTitle}
          </h2>
          <p className="mt-2 max-w-prose text-ink-muted">{INSIGHTS_COPY.notEnoughBody}</p>
          <p className="mt-2 text-sm text-ink-muted">
            {INSIGHTS_COPY.generatedLine(
              data.considered_check_ins,
              data.total_check_ins,
              data.timezone,
            )}{" "}
            {INSIGHTS_COPY.windowLine(data.window_days)}
          </p>
        </section>
      ) : null}

      {rollingPanels.map((panel) => (
        <InsightPanel
          key={panel.measure}
          testId={`insight-rolling-${panel.measure}`}
          heading={
            panel.measure === MEASURE_VALENCE
              ? INSIGHTS_COPY.panelMoodRolling
              : INSIGHTS_COPY.panelEnergyRolling
          }
          summary={rollingSummary(panel.windows, panel.label, data.scale_max)}
          note={INSIGHTS_COPY.windowLine(data.window_days)}
          chart={
            panel.windows.length === 0 ? undefined : (
              <RollingChart
                rows={rollingChartRows(panel.windows)}
                shortLabel={`${panel.shortest?.window_days ?? 7}-day average`}
                longLabel={`${panel.longest?.window_days ?? 30}-day average`}
              />
            )
          }
          table={rollingTable(panel.windows, panel.label)}
          csvTopic={`${panel.measure}-rolling`}
          today={today}
        />
      ))}

      <TablePanel
        testId="insight-trend"
        heading={INSIGHTS_COPY.panelTrend}
        summary={data.trend.measures.map((item) => `${item.label}: ${item.direction_label}`)}
        table={trendTable(data.trend)}
        note={
          data.trend.enough_data
            ? undefined
            : `Held back: ${data.trend.holdback ?? "not enough check-ins in the windows being compared"}.`
        }
      />

      {tagPanels.map((panel) => {
        const rows = differenceChartRows(data.factors.associations, panel.measure);
        const table = associationTable(
          data.factors.associations,
          panel.measure,
          panel.measure === MEASURE_VALENCE
            ? (data.trend.measures[0]?.label ?? "mood")
            : (data.trend.measures[1]?.label ?? "energy"),
        );
        const sentences = data.factors.associations
          .filter((item) => item.measure === panel.measure && item.sentence !== null)
          .map((item) => item.sentence as string);
        const measureLabel =
          panel.measure === MEASURE_VALENCE
            ? (data.trend.measures[0]?.label ?? "mood")
            : (data.trend.measures[1]?.label ?? "energy");

        return (
          <InsightPanel
            key={panel.measure}
            testId={`insight-tags-${panel.measure}`}
            heading={panel.heading}
            summary={sentences.length === 0 ? [INSIGHTS_COPY.nothingReported] : sentences}
            note={INSIGHTS_COPY.thresholdNote(
              data.factors.comparisons,
              data.factors.family_alpha,
              data.factors.min_group_n,
              data.factors.min_effect_points,
            )}
            chart={
              rows.length === 0 ? undefined : (
                <DifferenceChart rows={rows} measureLabel={measureLabel} />
              )
            }
            table={table}
            csvTopic={panel.topic}
            today={today}
          />
        );
      })}

      <GroupPanel
        testId="insight-time-of-day"
        heading={INSIGHTS_COPY.panelTimeOfDay}
        pattern={data.time_of_day}
        measureLabel={data.time_of_day.measure_label}
        scaleMax={data.scale_max}
        measure={MEASURE_VALENCE}
        csvTopic="parts-of-the-day"
        today={today}
      />
      <GroupPanel
        testId="insight-weekday"
        heading={INSIGHTS_COPY.panelWeekday}
        pattern={data.weekday}
        measureLabel={data.weekday.measure_label}
        scaleMax={data.scale_max}
        measure={MEASURE_VALENCE}
        csvTopic="days-of-the-week"
        today={today}
      />
    </>
  );
}

function GroupPanel({
  heading,
  pattern,
  measureLabel,
  scaleMax,
  measure,
  csvTopic,
  today,
  testId,
}: {
  heading: string;
  pattern: PatternInsights["time_of_day"];
  measureLabel: string;
  scaleMax: number;
  measure: string;
  csvTopic: string;
  today: string;
  testId: string;
}): ReactElement {
  return (
    <InsightPanel
      testId={testId}
      heading={heading}
      summary={[pattern.sentence]}
      chart={
        <BucketChart
          rows={bucketChartRows(pattern, measure)}
          measureLabel={measureLabel}
          named={{ lowest: pattern.lowest_id, highest: pattern.highest_id }}
          scaleMax={scaleMax}
        />
      }
      table={bucketTable(pattern)}
      csvTopic={csvTopic}
      today={today}
    />
  );
}

/**
 * Restate the rolling averages as text.
 *
 * This is the one place the client puts numbers into a sentence, and it is
 * allowed because it only restates: the average, how many check-ins it came from,
 * and which days. No direction, no comparison, no interpretation — that is the
 * trend panel's job, and those words come from the server.
 */
function rollingSummary(windows: readonly Rolling[], label: string, scaleMax: number): string[] {
  return windows.map((window) =>
    window.average === null
      ? INSIGHTS_COPY.rollingNotEnough(label, window.window_days)
      : INSIGHTS_COPY.rollingLine(
          label,
          window.window_days,
          formatPoints(window.average) ?? String(window.average),
          scaleMax,
          window.entries,
          formatLocalDay(window.start),
          formatLocalDay(window.end),
        ),
  );
}

// --------------------------------------------------------------------------- //
// Weekly                                                                      //
// --------------------------------------------------------------------------- #

function WeeklySection({ query }: { query: UseQueryResult<WeeklyInsights, Error> }): ReactElement {
  if (query.isPending) {
    return <StateNote>{INSIGHTS_COPY.loading}</StateNote>;
  }
  if (query.isError) {
    return (
      <ErrorPanel
        error={query.error}
        onRetry={() => {
          void query.refetch();
        }}
      />
    );
  }

  const data = query.data;
  const rows = weeklyChartRows(data.weekly);
  const valenceLabel = data.trend.measures[0]?.label ?? "mood";
  const energyLabel = data.trend.measures[1]?.label ?? "energy";

  return (
    <section aria-labelledby="insights-weekly-heading" className="space-y-4">
      <h2 id="insights-weekly-heading" className="text-xl">
        {INSIGHTS_COPY.sectionWeekly}
      </h2>

      <InsightPanel
        testId="insight-weekly"
        heading={INSIGHTS_COPY.panelWeekly}
        summary={data.summary}
        note={INSIGHTS_COPY.weeksLine(data.weeks)}
        chart={
          rows.length === 0 ? undefined : (
            <WeeklyChart
              rows={rows}
              valenceLabel={valenceLabel}
              energyLabel={energyLabel}
              scaleMax={data.scale_max}
            />
          )
        }
        table={weeklyTable(data.weekly)}
        csvTopic="week-by-week"
        today={data.today}
      />
    </section>
  );
}
