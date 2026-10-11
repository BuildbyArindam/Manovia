/**
 * Every user-facing string the insights surface can show, in one file.
 *
 * AGENTS.md asks for copy that is "warm, plain, short, non-judgemental" and free
 * of toxic positivity. On this surface that means:
 *
 * - **No causal language.** Associations are described as associations, never as
 *   causes. "On days you tagged poor sleep, your mood was lower on average."
 * - **No promises.** Patterns are descriptive, not prescriptive.
 * - **Honest limitations.** Every chart and summary has a note about minimum data
 *   requirements and what "not enough data" means.
 * - **No toxic positivity.** A downward trend is stated plainly, not reframed.
 */

import type { InsightsTrend } from "./types";

/** "1 day" / "3 days" without a plural library. */
export function pluralDays(count: number): string {
  return count === 1 ? "1 day" : `${count} days`;
}

export const INSIGHTS_COPY = {
  heading: "Insights",
  lede: "Patterns in your check-ins, shown honestly. No scores, no diagnoses — just what the data says.",

  // Weekly summary
  weeklyHeading: "This week",
  weeklyLoading: "Loading your weekly summary…",
  weeklyError: "Could not load the weekly summary. Please try again.",

  // Patterns
  patternsHeading: "Patterns",
  patternsLoading: "Analyzing your patterns…",
  patternsError: "Could not load patterns. Please try again.",
  chartsLoading: "Loading the charts…",

  // Rolling averages
  rollingHeading: "Mood trend",
  rollingSubheading: (days: number): string =>
    `${days}-day rolling average of how you feel (valence) and energy`,
  rollingValenceLabel: "Valence (1–5)",
  rollingEnergyLabel: "Energy (1–5)",
  rollingTrendUp: "Trending up",
  rollingTrendDown: "Trending down",
  rollingTrendFlat: "Stable",
  rollingTrendUnknown: "Not enough data for a trend",
  rollingTrendNote: (days: number): string =>
    `Need at least ${days} days with check-ins to show a trend. You have fewer.`,
  rollingNoData: "No check-ins in this window yet.",

  // Factor associations
  factorsHeading: "What goes with what",
  factorsSubheading:
    "Average mood on days with vs without each tag. This is association, not causation.",
  factorsNoData: (factor: string, min: number): string =>
    `Not enough days with ${factor} (need ${min} per group).`,
  factorsEffectSizeSmall: "small association",
  factorsEffectSizeMedium: "moderate association",
  factorsEffectSizeLarge: "strong association",
  factorsCI: "95% confidence interval for the difference",
  factorsPValue: "Permutation test p-value",
  factorsAssociativeNote: "This is an association, not a causal link.",

  // Time patterns
  timeHeading: "When mood shifts",
  hourlySubheading: "Average valence and energy by hour of day",
  weekdaySubheading: "Average valence and energy by day of week",
  timeNoData: (bucket: string, min: number): string =>
    `Only a few check-ins at ${bucket} — need ${min} to show a pattern.`,

  // About / limitations
  aboutHeading: "About these insights",
  aboutLimitations: [
    "These are descriptive patterns, not predictions or diagnoses.",
    "Associations do not imply causation — a tag co-occurs with a mood level, but we don't know why.",
    `Trends require at least ${7} days with check-ins in the window. Fewer days means "not enough data yet".`,
    `Factor comparisons need at least ${5} days per group (with and without the tag).`,
    "Time-of-day and weekday patterns use the same minimum-data rule.",
    "Your check-in notes are never used for these calculations — only the scales and tags.",
    "All calculations run on your data locally on the server; no external services are involved.",
  ],
  aboutDataSource: "Based on your mood check-ins. The more you check in, the clearer the patterns.",

  // Export
  exportButton: "Export as CSV",
  exportSuccess: "CSV files downloaded.",
  exportError: "Could not prepare the export. Please try again.",

  // Empty state
  emptyHeading: "No insights yet",
  emptyBody: "Check in for a few days to see patterns. The first ones appear after about a week.",

  // Chart accessibility
  chartAriaLabel: (title: string): string => `${title} chart`,
  chartDataTableLabel: "Data table alternative",
  chartNoData: "No data to display.",
} as const;

/** Trend label for display. */
export function trendLabel(trend: InsightsTrend): string {
  switch (trend) {
    case "up":
      return "Trending up";
    case "down":
      return "Trending down";
    case "flat":
      return "Stable";
    case "not_enough_data":
      return "Not enough data";
  }
}

/** All strings for copy review. */
export function allInsightsStrings(): string[] {
  const strings: string[] = [];
  for (const value of Object.values(INSIGHTS_COPY)) {
    if (typeof value === "string") {
      strings.push(value);
    }
  }
  // Function-valued entries
  strings.push(INSIGHTS_COPY.rollingSubheading(7));
  strings.push(INSIGHTS_COPY.rollingSubheading(30));
  strings.push(INSIGHTS_COPY.rollingTrendNote(7));
  strings.push(INSIGHTS_COPY.factorsNoData("sleep", 5));
  strings.push(INSIGHTS_COPY.timeNoData("10:00", 5));
  for (const trend of ["up", "down", "flat", "not_enough_data"] as const) {
    strings.push(trendLabel(trend));
  }
  strings.push(...INSIGHTS_COPY.aboutLimitations);
  return strings;
}
