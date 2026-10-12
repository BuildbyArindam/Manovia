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

import { t } from "@/lib/i18n/i18n";

import type { AssessmentBand } from "./types";

export function SCREENINGS_TITLE(): string {
  return t("assessments.title");
}
export function SCREENINGS_LEDE(): string {
  return t("assessments.lede");
}

export function SCREENINGS_NOTICE(): string {
  return t("assessments.notice");
}

export function ITEM9_BANNER_TITLE(): string {
  return t("assessments.item9BannerTitle");
}

/** How the answer choices are labelled for a screen reader. */
export function answerGroupLabel(position: number, itemText: string): string {
  return t("assessments.answerGroupLabel", { position, itemText });
}

/** The line under the questionnaire's submit button. */
export function SUBMIT_HINT(): string {
  return t("assessments.submitHint");
}

export function INCOMPLETE_ERROR_TITLE(): string {
  return t("assessments.incompleteErrorTitle");
}

export function incompleteErrorBody(count: number): string {
  return count === 1
    ? t("assessments.incompleteErrorBodyOne")
    : t("assessments.incompleteErrorBodyMany", { count });
}

export function SUBMIT_FAILED(): string {
  return t("assessments.submitFailed");
}

export function SCORE_HEADING(): string {
  return t("assessments.scoreHeading");
}

export function scoreLine(total: number, maxTotal: number, bandLabel: string): string {
  return t("assessments.scoreLine", { total, maxTotal, band: bandLabel.toLowerCase() });
}

export function WHAT_HELPS_HEADING(): string {
  return t("assessments.whatHelpsHeading");
}

export function WHEN_TO_TALK_HEADING(): string {
  return t("assessments.whenToTalkHeading");
}

export function PRINT_BUTTON(): string {
  return t("assessments.printButton");
}

export function PRINT_HINT(): string {
  return t("assessments.printHint");
}

export function HISTORY_HEADING(): string {
  return t("assessments.historyHeading");
}

export function TREND_HEADING(): string {
  return t("assessments.trendHeading");
}

export function TREND_TABLE_CAPTION(): string {
  return t("assessments.trendTableCaption");
}

export function TREND_CHART_NOTE(): string {
  return t("assessments.trendChartNote");
}

export function PROVENANCE_HEADING(): string {
  return t("assessments.provenanceHeading");
}

export function PROVENANCE_VERBATIM(): string {
  return t("assessments.provenanceVerbatim");
}

export function NOT_A_DIAGNOSIS_FALLBACK(): string {
  return t("assessments.notADiagnosisFallback");
}

/** The sentence that goes above a chart built from stored results. */
export function trendSummary(
  points: readonly { total: number; maxTotal: number; date: string }[],
): string {
  if (points.length === 0) {
    return t("assessments.trendEmpty");
  }
  const first = points[0];
  const latest = points[points.length - 1];
  if (first === undefined || latest === undefined) {
    return t("assessments.trendEmpty");
  }
  if (points.length === 1) {
    return t("assessments.trendOne", {
      latestTotal: latest.total,
      latestMax: latest.maxTotal,
      latestDate: latest.date,
    });
  }
  const change = latest.total - first.total;
  const direction =
    change === 0
      ? t("assessments.trendSame", { firstTotal: first.total })
      : t("assessments.trendDelta", {
          delta: Math.abs(change),
          points:
            Math.abs(change) === 1 ? t("assessments.trendPoint") : t("assessments.trendPoints"),
          direction: change < 0 ? t("assessments.trendLower") : t("assessments.trendHigher"),
          firstTotal: first.total,
        });
  const plural = points.length === 1 ? t("assessments.trendResult") : t("assessments.trendResults");
  return t("assessments.trendAcross", {
    count: points.length,
    results: plural,
    latestTotal: latest.total,
    latestMax: latest.maxTotal,
    direction,
  });
}

/** The item-9 line on the result page and the printout. */
export function ITEM9_SUMMARY(): string {
  return t("assessments.item9Summary");
}

/** A short line under the score for the band, used in the printable summary. */
export function bandLine(band: AssessmentBand, label: string, min: number, max: number): string {
  return t("assessments.bandLine", { label, band, min, max });
}
