/**
 * The page's own words: headings, buttons, and the sentences that describe what
 * the page is doing.
 *
 * What is *not* here, deliberately: any sentence about somebody's mood. The
 * insights copy — the summaries, the associative sentences, the explainer — is
 * written by the server from reviewed templates in the reader's language, and
 * arrives in the payload. This file holds the chrome around it. That division is
 * the reason a Hindi reader gets Hindi insight sentences at all: had the wording
 * lived here, it would have been English, next to a number, in a component.
 *
 * A consequence worth stating plainly: the chrome is English-only, because the
 * rest of the app's interface is too. A reader with `locale=hi` sees Hindi
 * sentences inside English headings. Localising the whole interface is a bigger
 * job and is in PROGRESS.md's parking lot rather than half-done here.
 */

export const INSIGHTS_COPY = {
  heading: "Insights",
  lede: "Patterns in your own check-ins, with the numbers behind every sentence.",

  loading: "Reading your check-ins…",
  errorTitle: "We could not read your insights",
  errorBody:
    "Nothing was lost. Your check-ins are still here; this page just could not reach them.",
  retry: "Try again",

  consentTitle: "Insights are switched off",
  consentBody:
    "This page reads your own check-ins back to you. It needs your agreement to the terms and the privacy notice first.",
  consentAction: "Review your consents",

  notEnoughTitle: "Not enough data yet",
  notEnoughBody:
    "There is nothing to describe yet, and this page will not invent a pattern. Check in a few more times and the same rules will have something to work with.",

  sectionPatterns: "Patterns in your check-ins",
  sectionWeekly: "Week by week",
  sectionAbout: "How this page works",

  panelMoodRolling: "Your mood, day by day",
  panelEnergyRolling: "Your energy, day by day",
  panelTrend: "Direction of travel",
  panelMoodTags: "Mood on days you tagged something",
  panelEnergyTags: "Energy on days you tagged something",
  panelTimeOfDay: "Parts of your day",
  panelWeekday: "Days of your week",
  panelWeekly: "Each week at a glance",

  /** The chart's own label, for the scrollable region around it. */
  chartRegion: (heading: string): string => `${heading}: chart, scrollable`,
  /** Every chart is decorative to an assistive technology: the text carries it. */
  chartDecorative: "Chart. The same numbers are in the text above and the table below.",

  showTable: "Show the data table",
  hideTable: "Hide the data table",
  downloadCsv: "Download CSV",
  downloadStarted: "CSV on its way to your downloads.",
  downloadBlocked:
    "This browser would not start a download. The table has the same numbers, and you can copy it from there.",
  tableNoteHeading: "How to read this table",

  nothingReported: "Nothing here cleared the rules for saying something.",

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
    `${label}: the ${windowDays}-day average is ${average} out of ${scaleMax}, from ${entries} check-ins between ${start} and ${end}.`,
  rollingNotEnough: (label: string, windowDays: number): string =>
    `${label}: not enough check-ins yet for a ${windowDays}-day average.`,

  /** The rules a tag comparison had to clear, in plain numbers. */
  thresholdNote: (
    comparisons: number,
    familyAlpha: number,
    minGroupN: number,
    minEffect: number,
  ): string =>
    `${comparisons} comparisons were run, so each one had to clear p ≤ ${familyAlpha.toFixed(
      4,
    )} rather than 0.05. A comparison also needs at least ${minGroupN} check-ins on each side and a difference of at least ${minEffect} points. The table below shows all of them, including the ones that did not clear.`,

  generatedLine: (considered: number, total: number, zone: string): string =>
    `Based on ${considered} of your ${total} check-ins, counted in ${zone}.`,
  windowLine: (days: number): string => `Looking back ${days} days.`,
  weeksLine: (weeks: number): string => `Looking back ${weeks} weeks.`,
  timezoneFallback: "Your browser would not name its timezone, so these are counted in UTC.",
  localeFallback: (locale: string): string =>
    `These insights are not translated into your language yet, so they are shown in ${locale}.`,
} as const;

export type InsightsCopy = typeof INSIGHTS_COPY;
