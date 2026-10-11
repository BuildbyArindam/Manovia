/**
 * The printable summary: the one artefact that leaves the app.
 *
 * It is hidden on screen and shown by `@media print` (see `index.css`), so
 * "Print or save as PDF" produces a page a person can hand to a clinician rather
 * than four screenshots of navigation chrome. What it must contain is therefore a
 * product requirement, not a styling choice:
 *
 * * the instrument, the date and the total with its maximum;
 * * the severity band **and** the sentence that says this is a screening result,
 *   not a diagnosis — the exact sentence, so a clinician reading a printout sees
 *   the same disclaimer the person saw;
 * * the full statement of what the instrument measures, its developer/copyright
 *   line and the licence (the PHQ-9 and GAD-7 terms allow reproduction, and
 *   saying so on the artefact is how a clinician can tell it is the real
 *   instrument);
 * * the item-9 line when it applies, because that is the single most important
 *   fact on the page for whoever reads it;
 * * the recent trend as a plain table, so it survives a black-and-white printer
 *   and needs no chart at all.
 */

import type { ReactElement } from "react";

import { formatPointDate, localDay } from "../trend";
import type { TrendPoint } from "../trend";
import type { AssessmentCrisis, AssessmentResultView } from "../types";
import { ITEM9_SUMMARY } from "../copy";

export interface PrintableSummaryProps {
  shortName: string;
  longName: string;
  measures: string;
  result: AssessmentResultView;
  crisis: AssessmentCrisis | null;
  /** Results for this instrument, oldest first. */
  points: readonly TrendPoint[];
  /** ISO date the summary was produced. */
  generatedOn: string;
  licenceStatement?: string | null;
  developers?: string | null;
}

export function PrintableSummary({
  shortName,
  longName,
  measures,
  result,
  crisis,
  points,
  generatedOn,
  licenceStatement,
  developers,
}: PrintableSummaryProps): ReactElement {
  return (
    <section className="print-summary" data-testid="print-summary" aria-hidden="true">
      <h2>Manovia screening summary — {shortName}</h2>
      <p>
        {longName}. This questionnaire asks about {measures} over the last two weeks.
      </p>
      <dl>
        <dt>Date of this result</dt>
        <dd>{formatPointDate(localDay(result.created_at))}</dd>
        <dt>Score</dt>
        <dd>
          {result.total} of {result.max_total} ({result.band_label})
        </dd>
      </dl>
      <p>
        <strong>{result.screening_not_diagnosis}</strong>
      </p>
      <p>{result.band_explanation}</p>
      {crisis !== null ? (
        <p>
          <strong>Important:</strong> {ITEM9_SUMMARY}
        </p>
      ) : null}
      <h3>What usually helps</h3>
      <ul>
        {result.what_helps.map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>
      <h3>When to talk to someone</h3>
      <p>{result.when_to_seek_help}</p>
      {points.length > 1 ? (
        <>
          <h3>Recent results</h3>
          <table>
            <caption>Every saved {shortName} result, oldest first.</caption>
            <thead>
              <tr>
                <th scope="col">Date</th>
                <th scope="col">Score</th>
                <th scope="col">Band</th>
              </tr>
            </thead>
            <tbody>
              {points.map((point) => (
                <tr key={point.id}>
                  <td>{formatPointDate(point.date)}</td>
                  <td>
                    {point.total} of {point.maxTotal}
                  </td>
                  <td>{point.bandLabel}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      ) : null}
      <h3>About this questionnaire</h3>
      <p>{developers}</p>
      {/* The licence sentence is what lets a clinician tell this is the real
          instrument rather than a paraphrase, so it travels with the printout. */}
      <p>
        {licenceStatement ??
          "Terms: no permission required to reproduce, translate, display or distribute."}
      </p>
      <p>
        Produced by Manovia on {formatPointDate(generatedOn)}. Manovia is a self-help companion: not
        therapy, not a diagnosis, and not a crisis service.
      </p>
    </section>
  );
}
