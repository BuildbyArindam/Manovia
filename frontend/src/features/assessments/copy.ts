/**
 * The words on the screening page, in one place so they can be reviewed as copy.
 *
 * Two rules from the brief are enforced by the strings here as much as by the
 * layout:
 *
 * * **No result-shaming.** Nothing says "bad", "abnormal", "failure" or
 *   "concerningly high". A score describes a fortnight; it is not a verdict on
 *   the person, and the copy says so before and after the number.
 * * **The disclaimer is not a footnote.** "This is a screening result, not a
 *   diagnosis" appears with the result itself and again on the printable
 *   summary, because the summary is the artefact that travels to a clinician.
 *
 * These sentences are English only, like the rest of the interface today; the
 * instrument's own wording and the crisis templates are localised by the server.
 */

import type { AssessmentBand } from "./types";

export const SCREENINGS_TITLE = "Screenings";
export const SCREENINGS_LEDE =
  "Two short, widely used questionnaires: the PHQ-9 for low mood and the GAD-7 for worry. They are screening tools, not tests you can pass or fail.";

export const SCREENINGS_NOTICE =
  "A screening result describes how the last two weeks have been. It is not a diagnosis, and it is not a measure of who you are.";

export const ITEM9_BANNER_TITLE = "Before your score, please read this";

/** How the answer choices are labelled for a screen reader. */
export function answerGroupLabel(position: number, itemText: string): string {
  return `Question ${position}. ${itemText}`;
}

/** The line under the questionnaire's submit button. */
export const SUBMIT_HINT =
  "Answer every question, then submit. Nothing is saved until you submit, and you can take it again whenever you like.";

export const INCOMPLETE_ERROR_TITLE = "Some questions are unanswered";

export function incompleteErrorBody(count: number): string {
  return count === 1
    ? "One question is still unanswered. Answer it and submit again."
    : `${count} questions are still unanswered. Answer them and submit again.`;
}

export const SUBMIT_FAILED = "We couldn't save your answers just now. Please try again.";

export const SCORE_HEADING = "Your result";

export function scoreLine(total: number, maxTotal: number, bandLabel: string): string {
  return `${total} of ${maxTotal} — ${bandLabel.toLowerCase()}`;
}

export const WHAT_HELPS_HEADING = "What usually helps";

export const WHEN_TO_TALK_HEADING = "When to talk to someone";

export const PRINT_BUTTON = "Print or save as PDF";

export const PRINT_HINT =
  "Use your browser's print dialog and choose “Save as PDF” to keep a copy or share it with a clinician.";

export const HISTORY_HEADING = "Past results";

export const TREND_HEADING = "Scores over time";

export const TREND_TABLE_CAPTION = "Every saved result for this questionnaire, oldest first.";

export const TREND_CHART_NOTE =
  "The dashed lines are the questionnaire's band boundaries. The table below has the same numbers.";

export const PROVENANCE_HEADING = "Where this questionnaire comes from";

export const PROVENANCE_VERBATIM =
  "The questions, the answer options and the scoring bands are reproduced exactly as published.";

export const NOT_A_DIAGNOSIS_FALLBACK =
  "This is a screening result, not a diagnosis. Only a qualified professional can diagnose, and they would do it by talking with you.";

/** The sentence that goes above a chart built from stored results. */
export function trendSummary(
  points: readonly { total: number; maxTotal: number; date: string }[],
): string {
  if (points.length === 0) {
    return "No saved results yet. Your first one will appear here.";
  }
  const first = points[0];
  const latest = points[points.length - 1];
  if (first === undefined || latest === undefined) {
    return "No saved results yet. Your first one will appear here.";
  }
  if (points.length === 1) {
    return `One result so far: ${latest.total} of ${latest.maxTotal} on ${latest.date}. A change needs at least two results to compare, so this is a starting point rather than a trend.`;
  }
  const change = latest.total - first.total;
  const direction =
    change === 0
      ? `the same as your first (${first.total})`
      : `${Math.abs(change)} point${Math.abs(change) === 1 ? "" : "s"} ${
          change < 0 ? "lower" : "higher"
        } than your first (${first.total})`;
  const plural = points.length === 1 ? "result" : "results";
  return `Across ${points.length} ${plural}, your latest score is ${latest.total} of ${latest.maxTotal} — ${direction}. Scores move for many reasons (sleep, stress, the time of day), so read the direction rather than the size.`;
}

/** The item-9 line on the result page and the printout. */
export const ITEM9_SUMMARY =
  "You answered above “Not at all” on the question about thoughts of being better off dead or of hurting yourself. That is why support and helplines were shown first, whatever your total score says.";

/** A short line under the score for the band, used in the printable summary. */
export function bandLine(band: AssessmentBand, label: string, min: number, max: number): string {
  return `${label} (${band}, scores ${min}–${max})`;
}
