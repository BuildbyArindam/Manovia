/**
 * The page's own words: headings, buttons, and the sentences that describe what
 * the page is doing.
 *
 * What is *not* here, deliberately: any sentence about somebody's mood. The
 * insights copy — the summaries, the associative sentences, the explainer — is
 * written by the server from reviewed templates in the reader's language, and
 * arrives in the payload. This file holds the chrome around it.
 *
 * Day 23: the chrome itself is localised too. A Hindi reader now gets Hindi
 * headings around Hindi sentences; the language this page asks the server for
 * follows the interface language (see `InsightsView`).
 */

import { t } from "@/lib/i18n/i18n";

export const INSIGHTS_COPY = {
  get heading() {
    return t("insights.heading");
  },
  get lede() {
    return t("insights.lede");
  },
  get loading() {
    return t("insights.loading");
  },
  get errorTitle() {
    return t("insights.errorTitle");
  },
  get errorBody() {
    return t("insights.errorBody");
  },
  get retry() {
    return t("insights.retry");
  },
  get consentTitle() {
    return t("insights.consentTitle");
  },
  get consentBody() {
    return t("insights.consentBody");
  },
  get consentAction() {
    return t("insights.consentAction");
  },
  get notEnoughTitle() {
    return t("insights.notEnoughTitle");
  },
  get notEnoughBody() {
    return t("insights.notEnoughBody");
  },
  get sectionPatterns() {
    return t("insights.sectionPatterns");
  },
  get sectionWeekly() {
    return t("insights.sectionWeekly");
  },
  get sectionAbout() {
    return t("insights.sectionAbout");
  },
  get panelMoodRolling() {
    return t("insights.panelMoodRolling");
  },
  get panelEnergyRolling() {
    return t("insights.panelEnergyRolling");
  },
  get panelTrend() {
    return t("insights.panelTrend");
  },
  get panelMoodTags() {
    return t("insights.panelMoodTags");
  },
  get panelEnergyTags() {
    return t("insights.panelEnergyTags");
  },
  get panelTimeOfDay() {
    return t("insights.panelTimeOfDay");
  },
  get panelWeekday() {
    return t("insights.panelWeekday");
  },
  get panelWeekly() {
    return t("insights.panelWeekly");
  },

  /** The chart's own label, for the scrollable region around it. */
  chartRegion: (heading: string): string => t("insights.chartRegion", { heading }),
  /** Every chart is decorative to an assistive technology: the text carries it. */
  get chartDecorative() {
    return t("insights.chartDecorative");
  },

  get showTable() {
    return t("insights.showTable");
  },
  get hideTable() {
    return t("insights.hideTable");
  },
  get downloadCsv() {
    return t("insights.downloadCsv");
  },
  get downloadStarted() {
    return t("insights.downloadStarted");
  },
  get downloadBlocked() {
    return t("insights.downloadBlocked");
  },
  get tableNoteHeading() {
    return t("insights.tableNoteHeading");
  },

  get nothingReported() {
    return t("insights.nothingReported");
  },

  /**
   * Restating the rolling averages as text: the numbers, how many check-ins they
   * came from, and which days. No direction and no comparison, because that is
   * the trend panel's job and those words come from the server.
   */
  rollingLine: (
    label: string,
    windowDays: number,
    average: string,
    scaleMax: number,
    entries: number,
    start: string,
    end: string,
  ): string =>
    t("insights.rollingLine", { label, windowDays, average, scaleMax, entries, start, end }),
  rollingNotEnough: (label: string, windowDays: number): string =>
    t("insights.rollingNotEnough", { label, windowDays }),

  /** The rules a tag comparison had to clear, in plain numbers. */
  thresholdNote: (
    comparisons: number,
    familyAlpha: number,
    minGroupN: number,
    minEffect: number,
  ): string =>
    t("insights.thresholdNote", {
      comparisons,
      familyAlpha: familyAlpha.toFixed(4),
      minGroupN,
      minEffect,
    }),

  generatedLine: (considered: number, total: number, zone: string): string =>
    t("insights.generatedLine", { considered, total, zone }),
  windowLine: (days: number): string => t("insights.windowLine", { days }),
  weeksLine: (weeks: number): string => t("insights.weeksLine", { weeks }),
  get timezoneFallback() {
    return t("insights.timezoneFallback");
  },
  localeFallback: (locale: string): string => t("insights.localeFallback", { locale }),

  get outlookTitle() {
    return t("insights.outlookTitle");
  },
  get outlookIntro() {
    return t("insights.outlookIntro");
  },
  get outlookOptInBody() {
    return t("insights.outlookOptInBody");
  },
  get outlookOpenSettings() {
    return t("insights.outlookOpenSettings");
  },
  get outlookCheckingSetting() {
    return t("insights.outlookCheckingSetting");
  },
  get outlookLoading() {
    return t("insights.outlookLoading");
  },
  get outlookSettingsError() {
    return t("insights.outlookSettingsError");
  },
  get outlookFetchError() {
    return t("insights.outlookFetchError");
  },
  get outlookRetry() {
    return t("insights.outlookRetry");
  },
  get outlookForecastHeading() {
    return t("insights.outlookForecastHeading");
  },
  get outlookTableCaption() {
    return t("insights.outlookTableCaption");
  },
  get outlookDay() {
    return t("insights.outlookDay");
  },
  get outlookEstimate() {
    return t("insights.outlookEstimate");
  },
  get outlookPossibleRange() {
    return t("insights.outlookPossibleRange");
  },
  outlookRangeValue: (lower: string, upper: string): string =>
    t("insights.outlookRangeValue", { lower, upper }),
  get outlookFactorsHeading() {
    return t("insights.outlookFactorsHeading");
  },
  get outlookSmallActionHeading() {
    return t("insights.outlookSmallActionHeading");
  },
  get outlookSupportHeading() {
    return t("insights.outlookSupportHeading");
  },
  get outlookTimezoneFallback() {
    return t("insights.outlookTimezoneFallback");
  },
  outlookLocaleFallback: (locale: string): string =>
    t("insights.outlookLocaleFallback", { locale }),
};

export type InsightsCopy = typeof INSIGHTS_COPY;
