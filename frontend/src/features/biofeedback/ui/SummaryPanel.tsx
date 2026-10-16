/**
 * Session summary and trend (Day 42).
 *
 * The summary describes what was observed in this session — pre/post RMSSD,
 * the person's own mood ratings, the pace — and carries the fixed caveats.
 * There are no thresholds, no colours for good or bad, and no conclusions:
 * HRV moves for many reasons and this panel says so every time.
 *
 * Saving is explicit and local: "Save summary" writes the encrypted record
 * (`store/summaryStore.ts`); nothing is uploaded. Raw RR data is never saved.
 *
 * WELLNESS FEATURE. Not a medical device.
 */

import type { ReactElement } from "react";

import { t } from "@/lib/i18n/i18n";

import type { SessionSummary, TrendPoint } from "../session/summary";

export interface SummaryPanelProps {
  summary: SessionSummary | null;
  trend: TrendPoint[];
  saved: boolean;
  onSave: () => void;
  onDeleteAll: () => void;
}

function moodLabel(mood: number | null): string {
  if (mood === null) {
    return "—";
  }
  return t(`biofeedback.session.mood${mood}`);
}

function metricLine(label: string, value: number | null, unit: string): string {
  return `${label}: ${value === null ? t("biofeedback.metrics.withheldShort") : `${value.toFixed(1)} ${unit}`}`;
}

export function SummaryPanel({
  summary,
  trend,
  saved,
  onSave,
  onDeleteAll,
}: SummaryPanelProps): ReactElement {
  return (
    <section
      className="space-y-4 rounded-2xl border border-border bg-surface p-6"
      aria-labelledby="biofeedback-summary-heading"
      data-testid="summary-panel"
    >
      <h2 id="biofeedback-summary-heading" className="text-xl">
        {t("biofeedback.summary.heading")}
      </h2>

      {summary === null ? (
        <p className="text-ink-muted" data-testid="summary-empty">
          {t("biofeedback.summary.empty")}
        </p>
      ) : (
        <div className="space-y-3" data-testid="summary-body">
          <p data-testid="summary-hrv">
            {t("biofeedback.summary.prePost", {
              pre:
                summary.pre?.rmssdMs === null || summary.pre === null
                  ? t("biofeedback.metrics.withheldShort")
                  : summary.pre.rmssdMs.toFixed(1),
              post:
                summary.post?.rmssdMs === null || summary.post === null
                  ? t("biofeedback.metrics.withheldShort")
                  : summary.post.rmssdMs.toFixed(1),
            })}
          </p>
          <p data-testid="summary-mood">
            {t("biofeedback.summary.moodLine", {
              pre: moodLabel(summary.preMood),
              post: moodLabel(summary.postMood),
            })}
          </p>
          <ul className="space-y-1 text-sm text-ink-muted">
            <li>{metricLine(t("biofeedback.summary.preHr"), summary.pre?.meanHrBpm ?? null, "bpm")}</li>
            <li>{metricLine(t("biofeedback.summary.postHr"), summary.post?.meanHrBpm ?? null, "bpm")}</li>
            <li>
              {t("biofeedback.summary.paceLine", {
                start: summary.paceRateStartBpm.toFixed(1),
                end: summary.paceRateEndBpm.toFixed(1),
              })}
            </li>
            {summary.stopReason === "dizzy" ? <li>{t("biofeedback.summary.dizzyNote")}</li> : null}
          </ul>
          <p className="rounded-xl border border-border bg-surface-muted p-3 text-sm" data-testid="summary-caveats">
            {t("biofeedback.summary.caveats")}
          </p>
          <div className="flex flex-wrap gap-3">
            <button
              type="button"
              className="rounded-xl bg-[var(--manovia-accent)] px-4 py-2 font-semibold text-white disabled:opacity-60"
              data-testid="summary-save"
              disabled={saved}
              onClick={onSave}
            >
              {saved ? t("biofeedback.summary.saved") : t("biofeedback.summary.save")}
            </button>
          </div>
          <p className="text-sm text-ink-muted">{t("biofeedback.summary.saveNote")}</p>
        </div>
      )}

      <div className="space-y-3">
        <h3 className="text-lg">{t("biofeedback.summary.trendHeading")}</h3>
        {trend.length === 0 ? (
          <p className="text-ink-muted" data-testid="trend-empty">
            {t("biofeedback.summary.noSessions")}
          </p>
        ) : (
          <>
            <table className="w-full text-sm" data-testid="trend-table">
              <thead>
                <tr className="text-left text-ink-muted">
                  <th className="py-1">{t("biofeedback.summary.trendDate")}</th>
                  <th className="py-1">{t("biofeedback.summary.trendPre")}</th>
                  <th className="py-1">{t("biofeedback.summary.trendPost")}</th>
                  <th className="py-1">{t("biofeedback.summary.trendMood")}</th>
                </tr>
              </thead>
              <tbody>
                {trend.map((point) => (
                  <tr key={point.date} className="border-t border-border">
                    <td className="py-1">{point.date.slice(0, 10)}</td>
                    <td className="py-1 tabular-nums">
                      {point.preRmssdMs === null ? "—" : point.preRmssdMs.toFixed(1)}
                    </td>
                    <td className="py-1 tabular-nums">
                      {point.postRmssdMs === null ? "—" : point.postRmssdMs.toFixed(1)}
                    </td>
                    <td className="py-1">
                      {moodLabel(point.preMood)} → {moodLabel(point.postMood)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="rounded-xl border border-border bg-surface-muted p-3 text-sm" data-testid="trend-caveat">
              {t("biofeedback.summary.trendCaveat")}
            </p>
            <button
              type="button"
              className="rounded-xl border border-border-strong px-4 py-2 font-semibold"
              data-testid="trend-delete-all"
              onClick={onDeleteAll}
            >
              {t("biofeedback.summary.deleteAll")}
            </button>
          </>
        )}
      </div>
    </section>
  );
}
