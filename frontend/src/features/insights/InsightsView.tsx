/**
 * Main insights view — weekly summary, patterns, and export.
 */

import { lazy, Suspense, useState, Fragment } from "react";
import { useQuery } from "@tanstack/react-query";

import type { ApiClient } from "@/lib/api";

import { fetchWeeklySummary, fetchInsightsPatterns } from "./endpoints";
import { WeeklySummaryCard } from "./WeeklySummary";
import { AboutInsights } from "./AboutInsights";
import { ExportButton } from "./ExportButton";
import { INSIGHTS_COPY } from "./copy";

/**
 * The three charts are lazy: Recharts is ~450 kB and only this page uses it, so
 * it must not sit in the bundle that loads the chat surface. `Suspense` shows the
 * same sentence the loading state uses — never an empty box.
 */
const RollingAverageChart = lazy(() =>
  import("./charts/RollingAverageChart").then((m) => ({ default: m.RollingAverageChart })),
);
const FactorAssociationChart = lazy(() =>
  import("./charts/FactorAssociationChart").then((m) => ({ default: m.FactorAssociationChart })),
);
const TimePatternChart = lazy(() =>
  import("./charts/TimePatternChart").then((m) => ({ default: m.TimePatternChart })),
);

export interface InsightsViewProps {
  /**
   * The API client, injected so tests can drive the real component against a
   * fake transport — the same seam `MoodView` and the chat feature use.
   */
  client?: ApiClient;
}

export function InsightsView({ client }: InsightsViewProps = {}) {
  const [weeklyLocale, setWeeklyLocale] = useState<"en" | "hi" | "bn">("en");

  const {
    data: weeklyData,
    isLoading: weeklyLoading,
    error: weeklyError,
  } = useQuery({
    queryKey: ["insights", "weekly", weeklyLocale],
    queryFn: () => fetchWeeklySummary({ locale: weeklyLocale }, client),
    staleTime: 5 * 60 * 1000, // 5 minutes
  });

  const {
    data: patternsData,
    isLoading: patternsLoading,
    error: patternsError,
  } = useQuery({
    queryKey: ["insights", "patterns"],
    queryFn: () => fetchInsightsPatterns(client),
    staleTime: 5 * 60 * 1000,
  });

  const hasAnyData =
    (weeklyData && weeklyData.total_check_ins > 0) ||
    (patternsData &&
      (patternsData.rolling_7.points.some((p) => p.count > 0) ||
        patternsData.factor_associations.some((a) => a.with_factor_count > 0)));

  return (
    <div style={{ padding: "24px", maxWidth: "900px", margin: "0 auto" }}>
      {/* Locale selector for weekly summary */}
      <div style={{ marginBottom: "24px", display: "flex", gap: "8px", alignItems: "center" }}>
        <label
          htmlFor="insights-locale"
          style={{ fontSize: "0.9rem", color: "var(--manovia-text-muted)" }}
        >
          Weekly summary language:
        </label>
        <select
          id="insights-locale"
          value={weeklyLocale}
          onChange={(e) => setWeeklyLocale(e.target.value as "en" | "hi" | "bn")}
          style={{
            padding: "8px 12px",
            background: "var(--manovia-surface)",
            border: "1px solid var(--manovia-border)",
            borderRadius: "8px",
            color: "var(--manovia-text)",
            fontSize: "0.9rem",
          }}
        >
          <option value="en">English</option>
          <option value="hi">हिन्दी</option>
          <option value="bn">বাংলা</option>
        </select>
      </div>

      {/* Weekly Summary */}
      <section aria-labelledby="weekly-heading" style={{ marginBottom: "32px" }}>
        <h2 id="weekly-heading" style={{ margin: "0 0 16px", fontSize: "1.3rem" }}>
          {INSIGHTS_COPY.weeklyHeading}
        </h2>
        <WeeklySummaryCard
          data={weeklyData ?? null}
          loading={weeklyLoading}
          error={weeklyError ? String(weeklyError) : null}
        />
      </section>

      {/* Patterns */}
      {hasAnyData ? (
        <Fragment>
          <section aria-labelledby="patterns-heading" style={{ marginBottom: "32px" }}>
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                marginBottom: "16px",
              }}
            >
              <h2 id="patterns-heading" style={{ margin: 0, fontSize: "1.3rem" }}>
                {INSIGHTS_COPY.patternsHeading}
              </h2>
              <ExportButton client={client} />
            </div>

            {patternsLoading ? (
              <div
                style={{
                  padding: "48px",
                  textAlign: "center",
                  color: "var(--manovia-text-muted)",
                }}
              >
                {INSIGHTS_COPY.patternsLoading}
              </div>
            ) : patternsError ? (
              <div
                style={{
                  padding: "24px",
                  background: "var(--manovia-surface-elevated)",
                  borderRadius: "12px",
                  border: "1px solid var(--manovia-border)",
                  color: "var(--manovia-error)",
                }}
              >
                <p style={{ margin: 0 }}>{INSIGHTS_COPY.patternsError}</p>
                <p
                  style={{
                    margin: "8px 0 0",
                    fontSize: "0.9rem",
                    color: "var(--manovia-text-muted)",
                  }}
                >
                  {String(patternsError)}
                </p>
              </div>
            ) : patternsData ? (
              <Suspense
                fallback={
                  <p role="status" style={{ color: "var(--manovia-text-muted)", padding: "24px" }}>
                    {INSIGHTS_COPY.chartsLoading}
                  </p>
                }
              >
                {/* Rolling averages */}
                <RollingAverageChart
                  data={patternsData.rolling_7}
                  title="7-day rolling average"
                  ariaLabel="7-day rolling average of valence and energy"
                />
                <RollingAverageChart
                  data={patternsData.rolling_30}
                  title="30-day rolling average"
                  ariaLabel="30-day rolling average of valence and energy"
                />

                {/* Factor associations */}
                <FactorAssociationChart
                  data={patternsData.factor_associations}
                  minPerGroup={patternsData.min_entries_per_group}
                  title="Factor associations"
                  ariaLabel="Bar chart comparing average mood on days with and without each context tag"
                />

                {/* Time patterns */}
                <TimePatternChart
                  data={patternsData.hourly_patterns}
                  title="Hourly patterns"
                  subheading="Average valence and energy by hour of day"
                  xAxisLabel="Hour"
                  ariaLabel="Bar chart showing average valence and energy for each hour of the day"
                />
                <TimePatternChart
                  data={patternsData.weekday_patterns}
                  title="Weekday patterns"
                  subheading="Average valence and energy by day of week"
                  xAxisLabel="Weekday"
                  ariaLabel="Bar chart showing average valence and energy for each day of the week"
                />
              </Suspense>
            ) : null}
          </section>

          {/* About / Limitations */}
          <AboutInsights />
        </Fragment>
      ) : (
        <div
          style={{
            padding: "48px",
            textAlign: "center",
            background: "var(--manovia-surface-elevated)",
            borderRadius: "12px",
            border: "1px solid var(--manovia-border)",
          }}
        >
          <h2 style={{ margin: "0 0 12px" }}>{INSIGHTS_COPY.emptyHeading}</h2>
          <p style={{ margin: 0, color: "var(--manovia-text-muted)", lineHeight: 1.5 }}>
            {INSIGHTS_COPY.emptyBody}
          </p>
        </div>
      )}
    </div>
  );
}
