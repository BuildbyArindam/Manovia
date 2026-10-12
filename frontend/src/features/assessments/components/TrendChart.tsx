/**
 * Scores over time: one line, hand-drawn in SVG, with the numbers beside it.
 *
 * Why not the chart library the insights page uses: it is large, insights is
 * lazily loaded precisely because of that, and a screening trend is a single
 * series of at most a few dozen integers. A small SVG keeps this route as cheap
 * as the rest of the app, and it keeps the accessibility story in one file.
 *
 * Honesty rules, borrowed from the Day 16 charts and kept deliberately:
 *
 * * **The y-axis is the instrument's own range** (0–27 or 0–21), never
 *   autoscaled. A line that fills the box because the axis starts at 9 would turn
 *   a two-point change into a cliff.
 * * **Band boundaries are drawn as labelled dashed rules**, so a reader can see
 *   which side of "moderate" a point sits on without a tooltip.
 * * **The chart is `aria-hidden`, the sentence and the table are not.** The
 *   summary sentence is derived from the same points the line is drawn from, and
 *   every point appears in a real table underneath — so the picture is a
 *   convenience, not the only way to read the data.
 * * **No hover-only affordance.** Nothing here needs a mouse.
 */

import { useId, type ReactElement } from "react";

import { TREND_CHART_NOTE, TREND_HEADING, TREND_TABLE_CAPTION, trendSummary } from "../copy";
import { cutpoints, formatPointDate } from "../trend";
import type { TrendPoint } from "../trend";
import type { BandSummary } from "../types";

export interface TrendChartProps {
  points: readonly TrendPoint[];
  bands: readonly BandSummary[];
  totalMax: number;
  /** Instrument name for the heading, e.g. `PHQ-9`. */
  shortName: string;
}

const WIDTH = 640;
const HEIGHT = 220;
const PADDING = { top: 16, right: 16, bottom: 32, left: 40 };

export function TrendChart({ points, bands, totalMax, shortName }: TrendChartProps): ReactElement {
  const headingId = useId();
  const captionId = useId();
  const plotWidth = WIDTH - PADDING.left - PADDING.right;
  const plotHeight = HEIGHT - PADDING.top - PADDING.bottom;
  const x = (index: number): number =>
    points.length <= 1
      ? PADDING.left + plotWidth / 2
      : PADDING.left + (index / (points.length - 1)) * plotWidth;
  const y = (total: number): number =>
    PADDING.top + plotHeight - (total / Math.max(totalMax, 1)) * plotHeight;
  const line = points.map((point, index) => `${x(index)},${y(point.total)}`).join(" ");

  return (
    <section
      className="mt-6 rounded-2xl border border-border bg-surface p-6 shadow-soft"
      aria-labelledby={headingId}
    >
      <h3 id={headingId} className="text-lg">
        {TREND_HEADING()}
      </h3>
      <p className="mt-2 max-w-prose text-ink-muted" data-testid="trend-summary">
        {trendSummary(points)}
      </p>

      {points.length > 0 ? (
        <div className="mt-4 overflow-x-auto">
          <svg
            viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
            role="presentation"
            aria-hidden="true"
            className="h-56 w-full min-w-[32rem]"
            data-testid="trend-chart"
          >
            {cutpoints(bands).map((cutpoint) => (
              <g key={cutpoint}>
                <line
                  x1={PADDING.left}
                  x2={WIDTH - PADDING.right}
                  y1={y(cutpoint)}
                  y2={y(cutpoint)}
                  className="stroke-border-strong"
                  strokeDasharray="4 4"
                />
                <text x={4} y={y(cutpoint) + 4} className="fill-ink-muted text-[11px]">
                  {cutpoint}
                </text>
              </g>
            ))}
            <line
              x1={PADDING.left}
              x2={WIDTH - PADDING.right}
              y1={y(0)}
              y2={y(0)}
              className="stroke-border"
            />
            <line
              x1={PADDING.left}
              x2={PADDING.left}
              y1={PADDING.top}
              y2={y(0)}
              className="stroke-border"
            />
            {points.length > 1 ? (
              <polyline
                points={line}
                fill="none"
                className="stroke-accent"
                strokeWidth={2.5}
                strokeLinejoin="round"
              />
            ) : null}
            {points.map((point, index) => (
              <g key={point.id}>
                <circle cx={x(index)} cy={y(point.total)} r={4.5} className="fill-accent" />
                <text
                  x={x(index)}
                  y={y(point.total) - 10}
                  textAnchor="middle"
                  className="fill-ink text-[12px]"
                >
                  {point.total}
                </text>
              </g>
            ))}
            <text x={PADDING.left} y={HEIGHT - 8} className="fill-ink-muted text-[11px]">
              {formatPointDate(points[0]?.date ?? "")}
            </text>
            <text
              x={WIDTH - PADDING.right}
              y={HEIGHT - 8}
              textAnchor="end"
              className="fill-ink-muted text-[11px]"
            >
              {formatPointDate(points[points.length - 1]?.date ?? "")}
            </text>
          </svg>
        </div>
      ) : null}

      <p className="mt-2 text-sm text-ink-muted">{TREND_CHART_NOTE()}</p>

      <table className="mt-4 w-full border-collapse text-sm" aria-describedby={captionId}>
        <caption id={captionId} className="pb-2 text-left text-ink-muted">
          {TREND_TABLE_CAPTION()} ({shortName})
        </caption>
        <thead>
          <tr className="border-b border-border-strong">
            <th scope="col" className="py-2 pr-4 text-left font-medium">
              Date
            </th>
            <th scope="col" className="py-2 pr-4 text-right font-medium">
              Score
            </th>
            <th scope="col" className="py-2 pr-4 text-left font-medium">
              Band
            </th>
          </tr>
        </thead>
        <tbody>
          {points.length === 0 ? (
            <tr>
              <td colSpan={3} className="py-3 text-ink-muted">
                No saved results yet.
              </td>
            </tr>
          ) : (
            points.map((point) => (
              <tr key={point.id} className="border-b border-border">
                <td className="py-2 pr-4">{formatPointDate(point.date)}</td>
                <td className="py-2 pr-4 text-right">
                  {point.total} of {point.maxTotal}
                </td>
                <td className="py-2 pr-4">{point.bandLabel}</td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </section>
  );
}
