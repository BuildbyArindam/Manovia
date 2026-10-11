/**
 * Dump every user-facing insights string as JSON, for `scripts/verify_day16.py`.
 *
 * The verification script must *see* the copy somebody reads, not a second copy
 * of it typed into Python: a scan of a Python file's idea of the copy is not a
 * scan of the copy. So the frontend hands its real strings over — every value of
 * `INSIGHTS_COPY`, with the function-valued entries expanded at representative
 * arguments — and the script does its own scanning and reporting.
 *
 * Run it from `frontend/` via esbuild (Vitest's `vite-node` is not installed):
 *
 *   npx esbuild scripts/insights-copy-dump.ts --bundle --format=esm --platform=node \
 *     --alias:@=./src --outfile=/tmp/insights-copy-dump.mjs && node /tmp/insights-copy-dump.mjs
 *
 * `scripts/verify_day16.py` does exactly that, so a human only ever runs the
 * Python script.
 */

import { INSIGHTS_COPY, allInsightsStrings, trendLabel } from "@/features/insights/copy";

const payload = {
  strings: [
    ...allInsightsStrings(),
    // The entries that exist only as functions, expanded the way the UI calls
    // them, so the review reads real sentences rather than a function reference.
    INSIGHTS_COPY.rollingSubheading(7),
    INSIGHTS_COPY.rollingSubheading(30),
    INSIGHTS_COPY.rollingTrendNote(7),
    INSIGHTS_COPY.factorsNoData("sleep", 5),
    INSIGHTS_COPY.factorsNoData("weather", 5),
    INSIGHTS_COPY.timeNoData("10:00", 5),
    INSIGHTS_COPY.chartAriaLabel("7-day rolling average"),
    trendLabel("up"),
    trendLabel("down"),
    trendLabel("flat"),
    trendLabel("not_enough_data"),
  ],
  // Words that are not sentences but still reach a person, listed separately so
  // the script can scan them with the vocabulary list rather than the sentence
  // list. A label like "Trending down" is a word somebody reads.
  labels: [
    INSIGHTS_COPY.rollingValenceLabel,
    INSIGHTS_COPY.rollingEnergyLabel,
    INSIGHTS_COPY.factorsEffectSizeSmall,
    INSIGHTS_COPY.factorsEffectSizeMedium,
    INSIGHTS_COPY.factorsEffectSizeLarge,
    INSIGHTS_COPY.factorsCI,
    INSIGHTS_COPY.factorsPValue,
    INSIGHTS_COPY.exportButton,
    INSIGHTS_COPY.chartDataTableLabel,
  ],
  limitations: [...INSIGHTS_COPY.aboutLimitations],
};

process.stdout.write(JSON.stringify(payload));
