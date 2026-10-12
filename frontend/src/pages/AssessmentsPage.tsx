/**
 * Screenings: pick a questionnaire, answer it, read the result, see the trend.
 *
 * One page rather than a route per instrument, because the flow is short and a
 * person should be able to change their mind mid-screen without losing what they
 * typed. The states are explicit — pick → answer → result — and so is the
 * consequence of the item-9 rule: when the crisis flow fires, `ResultView` puts
 * it above the score inside a single render, so there is no frame in which a
 * score exists without the help next to it.
 *
 * Nothing here diagnoses, scores a person's worth, or hides a result. The copy
 * for each band comes from the backend content file (reviewable, with its
 * provenance on the same screen), and the printable summary is a real section of
 * this page that print CSS reveals.
 */

import { useState, type ReactElement } from "react";

import { PageHeader } from "@/components/PageHeader";
import { t } from "@/lib/i18n/i18n";
import { api, ApiError, type ApiClient } from "@/lib/api";

import { QuestionnaireForm } from "@/features/assessments/components/QuestionnaireForm";
import { ResultView } from "@/features/assessments/components/ResultView";
import { PrintableSummary } from "@/features/assessments/components/PrintableSummary";
import { TrendChart } from "@/features/assessments/components/TrendChart";
import {
  PROVENANCE_HEADING,
  PROVENANCE_VERBATIM,
  SCREENINGS_LEDE,
  SCREENINGS_NOTICE,
  SCREENINGS_TITLE,
  SUBMIT_FAILED,
  incompleteErrorBody,
} from "@/features/assessments/copy";
import { fetchAssessmentResult } from "@/features/assessments/endpoints";
import { buildTrend } from "@/features/assessments/trend";
import type {
  AssessmentCrisis,
  AssessmentInstrument,
  AssessmentResultView,
  SubmitResponse,
} from "@/features/assessments/types";
import {
  useAssessmentDefinition,
  useAssessmentHistory,
  useInstruments,
  useSubmitAssessment,
} from "@/features/assessments/useAssessments";

interface Displayed {
  result: AssessmentResultView;
  crisis: AssessmentCrisis | null;
  cadence: SubmitResponse["cadence"] | null;
  historical: boolean;
}

function provenanceString(provenance: Record<string, unknown>, key: string): string | null {
  const value = provenance[key];
  return typeof value === "string" ? value : null;
}

export function AssessmentsPage({ client = api }: { client?: ApiClient }): ReactElement {
  const instruments = useInstruments(client);
  const [instrument, setInstrument] = useState<AssessmentInstrument | null>(null);
  const definition = useAssessmentDefinition(instrument, client);
  const history = useAssessmentHistory(instrument, client);
  const submit = useSubmitAssessment(instrument, client);

  const [answers, setAnswers] = useState<Record<string, number>>({});
  const [unanswered, setUnanswered] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [displayed, setDisplayed] = useState<Displayed | null>(null);

  const points = buildTrend(history.data?.entries ?? []);

  function choose(next: AssessmentInstrument): void {
    setInstrument(next);
    setAnswers({});
    setUnanswered([]);
    setError(null);
    setDisplayed(null);
  }

  async function onSubmit(): Promise<void> {
    if (definition.data === undefined) {
      return;
    }
    const missing = definition.data.items
      .map((item) => item.id)
      .filter((itemId) => answers[itemId] === undefined);
    setUnanswered(missing);
    setError(missing.length === 0 ? null : incompleteErrorBody(missing.length));
    if (missing.length > 0) {
      return;
    }
    try {
      const response = await submit.mutateAsync({ answers });
      setDisplayed({
        result: response.result,
        crisis: response.crisis,
        cadence: response.cadence,
        historical: false,
      });
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : SUBMIT_FAILED());
    }
  }

  async function openStored(resultId: string): Promise<void> {
    if (instrument === null) {
      return;
    }
    setError(null);
    try {
      const result = await fetchAssessmentResult(instrument, resultId, client);
      setDisplayed({ result, crisis: null, cadence: null, historical: true });
    } catch {
      setError(SUBMIT_FAILED());
    }
  }

  const definitionDoc = definition.data ?? null;

  return (
    <div className="space-y-6">
      <div data-print="hide">
        <PageHeader title={SCREENINGS_TITLE()} lede={SCREENINGS_LEDE()} />
        <p className="mt-4 max-w-prose text-ink-muted">{SCREENINGS_NOTICE()}</p>
        <p className="mt-2 max-w-prose text-sm text-ink-muted">{t("assessments.englishOnly")}</p>

        {instrument === null ? (
          <section className="mt-6 space-y-4" aria-label={t("assessments.chooseLabel")}>
            {instruments.isPending ? (
              <p className="text-ink-muted">{t("assessments.loadingList")}</p>
            ) : null}
            {instruments.isError ? (
              <p className="text-ink-muted">{t("assessments.loadError")}</p>
            ) : null}
            {(instruments.data ?? []).map((entry) => (
              <article
                key={entry.instrument}
                className="rounded-2xl border border-border bg-surface p-6 shadow-soft"
              >
                <h2 className="text-xl">{entry.name}</h2>
                <p className="mt-2 max-w-prose text-ink-muted">
                  {t("assessments.entrySummary", {
                    count: entry.item_count,
                    measures: entry.measures,
                    timeframe: entry.timeframe_label,
                  })}
                </p>
                <button
                  type="button"
                  onClick={() => {
                    choose(entry.instrument);
                  }}
                  className="mt-4 inline-flex min-h-12 items-center justify-center rounded-xl bg-accent px-5 py-3 font-semibold text-accent-fg"
                >
                  {t("assessments.startButton", { name: entry.short_name })}
                </button>
              </article>
            ))}
          </section>
        ) : null}

        {instrument !== null && displayed === null ? (
          <section className="mt-6" aria-labelledby="questionnaire-heading">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <h2 id="questionnaire-heading" className="text-xl">
                {definitionDoc?.name ?? "Questionnaire"}
              </h2>
              <button
                type="button"
                onClick={() => {
                  setInstrument(null);
                  setDisplayed(null);
                }}
                className="min-h-11 rounded-xl border border-border-strong bg-surface px-4 py-2 text-sm font-semibold"
              >
                Back to the questionnaires
              </button>
            </div>
            {definition.isPending ? <p className="mt-3 text-ink-muted">Loading…</p> : null}
            {definition.isError ? (
              <p className="mt-3 text-ink-muted">
                We couldn&rsquo;t load this questionnaire. Please refresh and try again.
              </p>
            ) : null}
            {definitionDoc !== null ? (
              <>
                <div className="mt-4">
                  <QuestionnaireForm
                    definition={definitionDoc}
                    answers={answers}
                    onChange={(itemId, value) => {
                      setAnswers((current) => ({ ...current, [itemId]: value }));
                    }}
                    onSubmit={() => {
                      void onSubmit();
                    }}
                    busy={submit.isPending}
                    unanswered={unanswered}
                    error={error}
                  />
                </div>
                <details className="mt-6 rounded-2xl border border-border bg-surface p-6">
                  <summary className="cursor-pointer font-semibold">{PROVENANCE_HEADING()}</summary>
                  <p className="mt-3 max-w-prose text-ink-muted">{PROVENANCE_VERBATIM()}</p>
                  <p className="mt-2 max-w-prose text-sm">
                    {provenanceString(definitionDoc.provenance, "developers")}
                  </p>
                  <p className="mt-1 max-w-prose text-sm">
                    {provenanceString(definitionDoc.provenance, "licence_statement")}
                  </p>
                  <p className="mt-1 max-w-prose text-sm text-ink-muted">
                    Source:{" "}
                    <a
                      className="underline"
                      href={
                        provenanceString(definitionDoc.provenance, "item_wording_source_url") ?? "#"
                      }
                      rel="noreferrer"
                      target="_blank"
                    >
                      {provenanceString(definitionDoc.provenance, "item_wording_source_url")}
                    </a>{" "}
                    · wording checked{" "}
                    {provenanceString(definitionDoc.provenance, "item_wording_verified_on")}
                  </p>
                </details>
              </>
            ) : null}
          </section>
        ) : null}

        {displayed !== null && definitionDoc !== null ? (
          <>
            <div className="mt-6">
              <button
                type="button"
                onClick={() => {
                  setDisplayed(null);
                  setAnswers({});
                  setUnanswered([]);
                  setError(null);
                }}
                className="min-h-11 rounded-xl border border-border-strong bg-surface px-4 py-2 text-sm font-semibold"
              >
                Answer again
              </button>
            </div>
            <div className="mt-6">
              <ResultView
                result={displayed.result}
                crisis={displayed.crisis}
                cadence={displayed.cadence}
                shortName={definitionDoc.short_name}
                historical={displayed.historical}
                onPrint={() => {
                  window.print();
                }}
              />
            </div>
            <TrendChart
              points={points}
              bands={history.data?.bands ?? definitionDoc.bands}
              totalMax={definitionDoc.total_max}
              shortName={definitionDoc.short_name}
            />
            {points.length > 1 ? (
              <section className="mt-6" aria-labelledby="history-heading">
                <h3 id="history-heading" className="text-lg">
                  Open a saved result
                </h3>
                <ul className="mt-3 space-y-2">
                  {[...points].reverse().map((point) => (
                    <li
                      key={point.id}
                      className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border bg-surface p-3"
                    >
                      <span>
                        {point.bandLabel} · {point.total} of {point.maxTotal}
                      </span>
                      <button
                        type="button"
                        onClick={() => {
                          void openStored(point.id);
                        }}
                        className="min-h-11 rounded-xl border border-border-strong px-4 py-2 text-sm font-semibold"
                      >
                        Open and print
                      </button>
                    </li>
                  ))}
                </ul>
              </section>
            ) : null}
            {error !== null ? (
              <p role="alert" className="mt-4 text-ink-muted">
                {error}
              </p>
            ) : null}
          </>
        ) : null}
      </div>

      {displayed !== null && definitionDoc !== null ? (
        <PrintableSummary
          shortName={definitionDoc.short_name}
          longName={definitionDoc.name}
          measures={definitionDoc.measures}
          result={displayed.result}
          crisis={displayed.crisis}
          points={points}
          generatedOn={new Date().toISOString().slice(0, 10)}
          licenceStatement={provenanceString(definitionDoc.provenance, "licence_statement")}
          developers={provenanceString(definitionDoc.provenance, "developers")}
        />
      ) : null}
    </div>
  );
}
