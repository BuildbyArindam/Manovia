/**
 * The tag-comparison chart: one bar per comparison the server is willing to
 * report, with its confidence interval drawn on it.
 *
 * The interval is not a flourish. A difference of 0.8 points with an interval
 * from 0.5 to 1.1 and a difference of 0.8 points with an interval from 0.05 to
 * 1.6 are different amounts of evidence, and a chart that shows only the first
 * number is asking the reader to trust a point estimate the app itself did not
 * trust — the server only reports a comparison when its interval clears zero.
 *
 * Bars start from a zero line in the middle of the axis, because these numbers
 * are *differences*: length to the left is "lower on days you tagged this",
 * length to the right is "higher". Colour agrees with direction (warm for lower,
 * cool for higher) but direction is also spelled out in the bar's own label and
 * in the table, so nobody depends on seeing the colour.
 *
 * Held-back comparisons are not drawn at all. A grey bar for "we could not say"
 * would still be a bar in a chart about differences, and the reason lives in the
 * table under the panel where it can be read properly.
 */

import type { ReactElement } from "react";
import { Bar, BarChart, Cell, ErrorBar, ReferenceLine, XAxis, YAxis } from "recharts";

import { usePrefersReducedMotion } from "@/lib/usePrefersReducedMotion";

import { CHART_COLORS, CHART_GEOMETRY, differenceColor } from "../palette";
import type { DifferenceRow } from "../series";

export interface DifferenceChartProps {
  rows: readonly DifferenceRow[];
  /** The measure's localised label, for the axis ("Mood"). */
  measureLabel: string;
}

interface PlottedRow {
  label: string;
  difference: number;
  interval: [number, number];
}

/** Half-width of the axis, so zero sits in the middle and both directions fit. */
function axisSpan(rows: readonly DifferenceRow[]): number {
  const widest = rows.reduce(
    (widestSoFar, row) =>
      Math.max(
        widestSoFar,
        Math.abs(row.difference),
        Math.abs(row.ciLow ?? row.difference),
        Math.abs(row.ciHigh ?? row.difference),
      ),
    0,
  );
  return Math.max(1, Math.ceil(widest * 2) / 2);
}

export function DifferenceChart({ rows, measureLabel }: DifferenceChartProps): ReactElement {
  const animate = !usePrefersReducedMotion();
  const plotted: PlottedRow[] = rows.map((row) => ({
    label: row.label,
    difference: row.difference,
    interval: [row.ciLow ?? row.difference, row.ciHigh ?? row.difference],
  }));
  const span = axisSpan(rows);

  return (
    <div data-testid="difference-chart">
      <BarChart
        layout="vertical"
        width={CHART_GEOMETRY.width}
        height={Math.max(140, plotted.length * 56 + 40)}
        data={plotted}
        margin={{ ...CHART_GEOMETRY.margin, left: 8 }}
      >
        <XAxis
          type="number"
          domain={[-span, span]}
          tick={{ fontSize: 12, fill: CHART_COLORS.axis }}
          stroke={CHART_COLORS.axis}
          label={{
            value: `${measureLabel} difference (points)`,
            position: "insideBottom",
            offset: -2,
            fontSize: 12,
            fill: CHART_COLORS.axis,
          }}
        />
        <YAxis
          type="category"
          dataKey="label"
          width={88}
          tick={{ fontSize: 12, fill: CHART_COLORS.axis }}
          stroke={CHART_COLORS.axis}
        />
        <ReferenceLine x={0} stroke={CHART_COLORS.axis} strokeWidth={1.5} />
        <Bar dataKey="difference" isAnimationActive={animate} barSize={18}>
          {plotted.map((row) => (
            <Cell key={row.label} fill={differenceColor(row.difference)} />
          ))}
          <ErrorBar
            dataKey="interval"
            direction="x"
            stroke={CHART_COLORS.muted}
            strokeWidth={2}
            width={9}
          />
        </Bar>
      </BarChart>
      <p className="mt-2 text-xs text-ink-muted">
        Left of the line means lower on days with that tag; right means higher. The whisker is the
        95% interval for the difference.
      </p>
    </div>
  );
}
