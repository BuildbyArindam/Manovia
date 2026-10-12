/**
 * A grouping chart: one bar per part of the day, or per weekday.
 *
 * **Bars start at zero.** These are bars, and a bar's only honest encoding is its
 * length; an axis truncated at 1 would make a 0.3-point difference between two
 * buckets look like a cliff. (The rolling chart is the opposite case: a line
 * encodes *position*, not length, so its axis runs 1 to 5 — the range a check-in
 * can actually take — and a truncated axis there distorts nothing.)
 *
 * **Every bucket is drawn, including the ones too small to name.** A bucket with
 * three check-ins is muted rather than removed: it is somebody's own number, and
 * the table under the panel lists it with its count. What the app refuses to do
 * is *say* something about it — the summary sentence names only buckets that
 * cleared the minimum, and only when the grouping test cleared its threshold.
 *
 * The two buckets a sentence does name are drawn in the highlight colour, so the
 * picture and the sentence point at the same things.
 */

import type { ReactElement } from "react";
import { Bar, BarChart, Cell, XAxis, YAxis } from "recharts";

import { usePrefersReducedMotion } from "@/lib/usePrefersReducedMotion";

import { CHART_COLORS, CHART_GEOMETRY, bucketColor } from "../palette";
import type { BucketRow } from "../series";

export interface BucketChartProps {
  rows: readonly BucketRow[];
  /** Localised label of the measure being drawn ("Mood"). */
  measureLabel: string;
  /** Which buckets the summary sentence named, by id. */
  named: { lowest: string | null; highest: string | null };
  /** The top of the scale, from the response rather than assumed to be 5. */
  scaleMax: number;
}

export function BucketChart({
  rows,
  measureLabel,
  named,
  scaleMax,
}: BucketChartProps): ReactElement {
  const animate = !usePrefersReducedMotion();

  return (
    <div data-testid="bucket-chart">
      <BarChart
        accessibilityLayer={false}
        width={CHART_GEOMETRY.width}
        height={CHART_GEOMETRY.barHeight}
        data={rows.map((row) => ({ ...row }))}
        margin={{ ...CHART_GEOMETRY.margin }}
      >
        <XAxis
          dataKey="label"
          interval={0}
          tick={{ fontSize: 12, fill: CHART_COLORS.axis }}
          stroke={CHART_COLORS.axis}
        />
        <YAxis
          domain={[0, scaleMax]}
          tick={{ fontSize: 12, fill: CHART_COLORS.axis }}
          stroke={CHART_COLORS.axis}
          width={32}
          tickCount={scaleMax + 1}
          label={{
            value: measureLabel,
            angle: -90,
            position: "insideLeft",
            fontSize: 12,
            fill: CHART_COLORS.axis,
          }}
        />
        <Bar dataKey="mean" isAnimationActive={animate} barSize={34}>
          {rows.map((row) => (
            <Cell key={row.id} fill={bucketColor(row.id, named, row.enoughData)} />
          ))}
        </Bar>
      </BarChart>
      <p className="mt-2 text-xs text-ink-muted">
        The highlighted bars are the two the sentence above names. A muted bar has too few check-ins
        to say anything about; it is still in the table.
      </p>
    </div>
  );
}
