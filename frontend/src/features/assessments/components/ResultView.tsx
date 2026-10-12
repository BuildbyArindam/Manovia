/**
 * The result: help first when item 9 was answered, then the score.
 *
 * The order is the safety property, and it is enforced by the DOM rather than by
 * styling: when the item-9 flow fired, the crisis card is the **first** element
 * in this section, before the number, and the test asserts it with
 * `compareDocumentPosition`. Somebody who has just told us they think about
 * being better off dead should not have to scroll past a score to find a phone
 * number.
 *
 * The rest is tone as much as layout:
 *
 * * The score is a number and a band, in the accent colour, never a red alarm —
 *   mid-range results are common and treatable, and colouring them as emergencies
 *   would either frighten people or teach them to ignore the colour that matters.
 * * The band's own sentences come from the content file, so what a person reads
 *   about "moderately severe" is the reviewed copy, not something a component
 *   invented.
 * * "This is a screening result, not a diagnosis" sits with the score, not in the
 *   footer.
 */

import type { ReactElement } from "react";

import { CrisisCard } from "@/components/CrisisCard";

import {
  ITEM9_BANNER_TITLE,
  ITEM9_SUMMARY,
  PRINT_BUTTON,
  PRINT_HINT,
  SCORE_HEADING,
  WHAT_HELPS_HEADING,
  WHEN_TO_TALK_HEADING,
  scoreLine,
} from "../copy";
import type { AssessmentCrisis, AssessmentResultView, CadenceView } from "../types";

export interface ResultViewProps {
  result: AssessmentResultView;
  crisis: AssessmentCrisis | null;
  cadence: CadenceView | null;
  /** Screen name of the instrument, e.g. `PHQ-9`. */
  shortName: string;
  onPrint: () => void;
  /** True when the score belongs to a stored run opened from history. */
  historical?: boolean;
}

export function ResultView({
  result,
  crisis,
  cadence,
  shortName,
  onPrint,
  historical = false,
}: ResultViewProps): ReactElement {
  return (
    <section data-testid="assessment-result" aria-labelledby="result-heading">
      {crisis !== null ? (
        <div data-testid="crisis-block" className="mb-6">
          <h2 id="item9-heading" className="text-xl sm:text-2xl">
            {ITEM9_BANNER_TITLE()}
          </h2>
          <p className="mt-2 max-w-prose text-ink-muted">{crisis.explanation}</p>
          <div className="mt-4">
            <CrisisCard
              message={crisis.message}
              resources={crisis.resources}
              emergency={crisis.emergency}
              disclaimer={crisis.message?.disclaimer ?? null}
              heading={null}
              testId="assessment-crisis"
            />
          </div>
          <p className="mt-3 max-w-prose">{crisis.encouragement}</p>
          <p className="mt-2 max-w-prose text-sm text-ink-muted">{crisis.paired_with_score}</p>
        </div>
      ) : null}

      <div data-print="hide">
        <div
          data-testid="score-block"
          className="rounded-2xl border border-border bg-surface p-6 shadow-soft sm:p-8"
        >
          <h2 id="result-heading" className="text-xl sm:text-2xl">
            {SCORE_HEADING()}
          </h2>
          <p className="mt-1 text-sm text-ink-muted">
            {shortName} · {historical ? "a saved result" : "just submitted"}
          </p>
          <p className="mt-4 text-3xl font-semibold text-accent">
            {scoreLine(result.total, result.max_total, result.band_label)}
          </p>
          <p className="mt-4 max-w-prose text-lg">{result.band_explanation}</p>
          <p
            className="mt-4 max-w-prose rounded-xl bg-accent-soft p-4"
            data-testid="not-a-diagnosis"
          >
            <strong className="font-semibold">{result.screening_not_diagnosis}</strong>{" "}
            {result.how_to_read}
          </p>
        </div>

        {historical &&
        result.answers.some((answer) => answer.value > 0 && answer.item_id.endsWith("_9")) ? (
          <p className="mt-4 max-w-prose rounded-xl border border-border-strong bg-surface-muted p-4 text-sm">
            {ITEM9_SUMMARY()}
          </p>
        ) : null}

        <section className="mt-6 rounded-2xl border border-border bg-surface p-6 shadow-soft">
          <h3 className="text-lg">{WHAT_HELPS_HEADING()}</h3>
          <ul className="mt-3 list-disc space-y-2 pl-5">
            {result.what_helps.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </section>

        <section className="mt-6 rounded-2xl border border-border bg-surface p-6 shadow-soft">
          <h3 className="text-lg">{WHEN_TO_TALK_HEADING()}</h3>
          <p className="mt-3 max-w-prose">{result.when_to_seek_help}</p>
        </section>

        {cadence !== null ? (
          <p data-testid="cadence-notice" className="mt-6 max-w-prose text-sm text-ink-muted">
            {cadence.notice}
          </p>
        ) : null}

        <div className="mt-6">
          <button
            type="button"
            onClick={onPrint}
            className="inline-flex min-h-12 items-center justify-center rounded-xl border border-border-strong bg-surface px-5 py-3 font-semibold"
          >
            {PRINT_BUTTON()}
          </button>
          <p className="mt-2 max-w-prose text-sm text-ink-muted">{PRINT_HINT()}</p>
        </div>
      </div>
    </section>
  );
}
