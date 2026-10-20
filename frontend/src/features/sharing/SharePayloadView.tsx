/**
 * The decrypted document, on screen.
 *
 * This is the version that can render any script: the PDF uses a built-in Latin
 * font and has to substitute characters it cannot draw, so the on-screen copy is
 * the complete one and says so. Everything here is drawn from the decrypted
 * payload — no field is invented, and nothing is added that the person did not
 * tick.
 *
 * The banner comes from the document itself (`disclaimer_title`, `disclaimer`),
 * not from this component's own copy, so what the viewer reads is what the
 * owner confirmed and what the PDF prints.
 */

import type { ReactElement } from "react";

import { formatDateTime } from "@/lib/dates";
import { t } from "@/lib/i18n/i18n";

import { RECORD_SECTIONS, type SharePayload, type ThoughtRecordShare } from "./types";

export interface SharePayloadViewProps {
  payload: SharePayload;
}

export function SharePayloadView({ payload }: SharePayloadViewProps): ReactElement {
  return (
    <div className="space-y-6" data-testid="share-payload">
      <Banner title={payload.disclaimer_title} body={payload.disclaimer} />

      <p className="text-sm text-ink-muted" data-testid="share-payload-generated">
        {t("sharing.viewerGeneratedOn", { date: formatDateTime(payload.generated_at) })}
      </p>

      <section className="card" aria-labelledby="share-payload-contents">
        <h3 id="share-payload-contents" className="font-semibold">
          {t("sharing.viewerItemsHeading")}
        </h3>
        <p className="mt-1 text-sm text-ink-muted" data-testid="share-payload-identity">
          {payload.identity === null
            ? t("sharing.viewerAnonymous")
            : `${t("sharing.viewerIdentityLabel")}: ${payload.identity}`}
        </p>
        <ul className="mt-2 list-disc pl-5 text-sm" data-testid="share-payload-never">
          {payload.never_included.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      </section>

      {payload.mood_trend !== null ? (
        <section className="card" aria-labelledby="share-mood-heading">
          <h3 id="share-mood-heading" className="font-semibold">
            {t("sharing.viewerMoodHeading")}
          </h3>
          <dl className="mt-2 grid gap-2 text-sm sm:grid-cols-2" data-testid="share-mood">
            <Fact label={t("sharing.windowLabel")} value={`${payload.mood_trend.window_days}`} />
            <Fact label="check-ins" value={`${payload.mood_trend.entries}`} />
            <Fact label="mean mood (1-5)" value={formatNumber(payload.mood_trend.mean_valence)} />
            <Fact label="mean energy (1-5)" value={formatNumber(payload.mood_trend.mean_energy)} />
          </dl>
          <p className="mt-2 text-sm" data-testid="share-mood-direction">
            {payload.mood_trend.direction_note}
          </p>
          {payload.mood_trend.top_emotions.length > 0 ? (
            <p className="mt-1 text-sm text-ink-muted" data-testid="share-mood-emotions">
              {payload.mood_trend.top_emotions
                .map((tag) => `${tag.label} (${tag.count})`)
                .join(", ")}
            </p>
          ) : null}
        </section>
      ) : null}

      {payload.assessments !== null && payload.assessments.length > 0 ? (
        <section className="card" aria-labelledby="share-assessments-heading">
          <h3 id="share-assessments-heading" className="font-semibold">
            {t("sharing.viewerAssessmentsHeading")}
          </h3>
          <table className="mt-2 w-full text-sm" data-testid="share-assessments">
            <caption className="sr-only">{t("sharing.viewerAssessmentsHeading")}</caption>
            <thead>
              <tr className="text-left">
                <th scope="col">Date</th>
                <th scope="col">Questionnaire</th>
                <th scope="col">Total</th>
                <th scope="col">Band</th>
                <th scope="col">LOINC</th>
              </tr>
            </thead>
            <tbody>
              {payload.assessments.map((assessment, index) => (
                <tr key={`${assessment.instrument}-${index}`} data-testid="share-assessment-row">
                  <td>{formatDateTime(assessment.completed_at)}</td>
                  <td>{assessment.instrument_name}</td>
                  <td>
                    {assessment.total} {assessment.ucum_unit}
                  </td>
                  <td>
                    {assessment.band_label}
                    <span className="block text-xs text-ink-muted">not a diagnosis</span>
                  </td>
                  <td>
                    <a
                      className="underline"
                      href={assessment.loinc_source_url}
                      rel="noreferrer noopener"
                      target="_blank"
                      data-testid="share-loinc-link"
                    >
                      {assessment.loinc_code}
                    </a>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ) : null}

      {payload.thought_records !== null ? (
        <section className="card space-y-4" aria-labelledby="share-records-heading">
          <h3 id="share-records-heading" className="font-semibold">
            {t("sharing.viewerRecordsHeading")}
          </h3>
          {payload.thought_records.map((record) => (
            <RecordView key={record.id} record={record} />
          ))}
        </section>
      ) : null}

      {payload.exercise_helpfulness !== null ? (
        <section className="card" aria-labelledby="share-exercise-heading">
          <h3 id="share-exercise-heading" className="font-semibold">
            {t("sharing.viewerExerciseHeading")}
          </h3>
          <table className="mt-2 w-full text-sm" data-testid="share-exercise">
            <caption className="sr-only">{t("sharing.viewerExerciseHeading")}</caption>
            <thead>
              <tr className="text-left">
                <th scope="col">Exercise</th>
                <th scope="col">Times</th>
                <th scope="col">Mean before</th>
                <th scope="col">Mean after</th>
                <th scope="col">Mean change</th>
              </tr>
            </thead>
            <tbody>
              {payload.exercise_helpfulness.exercises.map((row) => (
                <tr key={row.title} data-testid="share-exercise-row">
                  <td>{row.title}</td>
                  <td>{row.times}</td>
                  <td>{formatNumber(row.mean_before)}</td>
                  <td>{formatNumber(row.mean_after)}</td>
                  <td>{formatSigned(row.mean_change)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="mt-2 text-sm text-ink-muted" data-testid="share-exercise-scale">
            {payload.exercise_helpfulness.rating_scale}.{" "}
            {payload.exercise_helpfulness.change_caveat}
          </p>
          {payload.exercise_helpfulness.most_helpful !== null ? (
            <p className="mt-1 text-sm" data-testid="share-exercise-most-helpful">
              {payload.exercise_helpfulness.most_helpful}
            </p>
          ) : null}
        </section>
      ) : null}

      {payload.note !== null && payload.note !== "" ? (
        <section className="card" aria-labelledby="share-note-heading">
          <h3 id="share-note-heading" className="font-semibold">
            {t("sharing.viewerNoteHeading")}
          </h3>
          <p
            className="mt-2 whitespace-pre-wrap text-sm"
            data-testid="share-note-text"
            lang={undefined}
          >
            {payload.note}
          </p>
        </section>
      ) : null}

      {payload.questions !== null && payload.questions.length > 0 ? (
        <section className="card" aria-labelledby="share-questions-heading">
          <h3 id="share-questions-heading" className="font-semibold">
            {t("sharing.viewerQuestionsHeading")}
          </h3>
          <ol className="mt-2 list-decimal space-y-1 pl-5 text-sm" data-testid="share-questions">
            {payload.questions.map((question) => (
              <li key={question}>{question}</li>
            ))}
          </ol>
        </section>
      ) : null}

      <p className="text-sm text-ink-muted" data-testid="share-viewer-footer">
        {t("sharing.viewerFooter")}
      </p>
    </div>
  );
}

/** The boxed statement that opens and closes every version of this document. */
export function Banner({ title, body }: { title: string; body: string }): ReactElement {
  return (
    <div
      className="rounded-2xl border-2 border-accent-bg bg-surface p-4"
      role="note"
      data-testid="share-banner"
    >
      <p className="font-semibold" data-testid="share-banner-title">
        {title}
      </p>
      <p className="mt-1 text-sm text-ink-muted" data-testid="share-banner-body">
        {body}
      </p>
    </div>
  );
}

function Fact({ label, value }: { label: string; value: string }): ReactElement {
  return (
    <div>
      <dt className="text-ink-muted">{label}</dt>
      <dd className="font-semibold">{value}</dd>
    </div>
  );
}

function RecordView({ record }: { record: ThoughtRecordShare }): ReactElement {
  return (
    <article className="space-y-2" data-testid={`share-record-${record.id}`}>
      <h4 className="text-sm font-semibold">
        {formatDateTime(record.created_at)}
        {record.emotion !== null ? ` · ${record.emotion}` : ""}
        {record.intensity_before !== null && record.intensity_after !== null
          ? ` · ${record.intensity_before}/100 → ${record.intensity_after}/100`
          : ""}
      </h4>
      {RECORD_SECTIONS.map(([field, key]) => {
        const value = record[field];
        if (value.trim() === "") {
          return null;
        }
        return (
          <div key={field}>
            <h5 className="text-sm font-semibold">{t(key)}</h5>
            <p className="whitespace-pre-wrap text-sm" data-testid={`share-record-${field}`}>
              {value}
            </p>
          </div>
        );
      })}
      {record.what_helped.length > 0 ? (
        <p className="text-sm text-ink-muted" data-testid="share-record-helped">
          {record.what_helped.join(", ")}
        </p>
      ) : null}
    </article>
  );
}

function formatNumber(value: number | null): string {
  return value === null ? "n/a" : value.toFixed(2).replace(/\.00$/, "");
}

function formatSigned(value: number | null): string {
  if (value === null) {
    return "n/a";
  }
  const rounded = value.toFixed(2).replace(/\.00$/, "");
  return value > 0 ? `+${rounded}` : rounded;
}
