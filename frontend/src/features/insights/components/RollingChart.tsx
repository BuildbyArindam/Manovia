/**
 * The rolling-average line chart: two windows of the same measure over 56 days.
 *
 * Two decisions here are about honesty rather than looks.
 *
 * **Gaps stay gaps.** `connectNulls` is off, so a stretch where a window had too
 * few check-ins shows as a break in the line. Joining the dots across a fortnight
 * of silence would draw a trend the data does not contain, and it would do it in
 * the one place a reader is most likely to trust — a picture.
 *
 * **The two lines differ by more than colour.** One is solid, one is dashed, and
 * the legend says which is which in words. For a deuteranope the orange and the
 * blue are far apart anyway (measured in `palette.test.ts`), but the dash pattern
 * is what makes the chart survive greyscale printing and a colour theme nobody
 * predicted.
 *
 * Animation is switched off when the reader asked for reduced motion, and there
 * is no tooltip: a hover-only affordance is a mouse-only affordance, and the same
 * numbers are in the table under the panel.
 */

import type { ReactElement } from "react";
import { CartesianGrid, Line, LineChart, XAxis, YAxis } from "recharts";

import { formatShortDay } from "@/lib/dates";
import { usePrefersReducedMotion } from "@/lib/usePrefersReducedMotion";

import { CHART_COLORS, CHART_GEOMETRY, SERIES_STYLES, dashPattern } from "../palette";
import type { RollingRow } from "../series";

export interface RollingChartProps {
  rows: readonly RollingRow[];
  /** Legend text for the shorter window ("7-day average"). */
  shortLabel: string;
  /** Legend text for the longer window ("30-day average"). */
  longLabel: string;
}

const AXIS_TICK = { fontSize: 12, fill: CHART_COLORS.axis } as const;

export function RollingChart({ rows, shortLabel, longLabel }: RollingChartProps): ReactElement {
  const animate = !usePrefersReducedMotion();

  return (
    <div data-testid="rolling-chart">
      <LineChart
        width={CHART_GEOMETRY.width}
        height={CHART_GEOMETRY.height}
        data={[...rows]}
        margin={{ ...CHART_GEOMETRY.margin }}
      >
        <CartesianGrid stroke={CHART_COLORS.axis} strokeDasharray="2 4" vertical={false} />
        <XAxis
          dataKey="day"
          tick={AXIS_TICK}
          tickFormatter={formatShortDay}
          minTickGap={48}
          stroke={CHART_COLORS.axis}
        />
        <YAxis
          domain={[CHART_GEOMETRY.domainMin, CHART_GEOMETRY.domainMax]}
          tick={AXIS_TICK}
          stroke={CHART_COLORS.axis}
          width={32}
          tickCount={5}
        />
        <Line
          type="monotone"
          dataKey="short"
          name={shortLabel}
          stroke={CHART_COLORS.shortWindow}
          strokeWidth={2.5}
          strokeDasharray={dashPattern(SERIES_STYLES.shortWindow)}
          dot={false}
          connectNulls={false}
          isAnimationActive={animate}
          legendType="plainline"
        />
        <Line
          type="monotone"
          dataKey="long"
          name={longLabel}
          stroke={CHART_COLORS.longWindow}
          strokeWidth={2.5}
          strokeDasharray={dashPattern(SERIES_STYLES.longWindow)}
          dot={false}
          connectNulls={false}
          isAnimationActive={animate}
          legendType="plainline"
        />
      </LineChart>
      <ul className="mt-2 flex flex-wrap gap-4 text-xs text-ink-muted">
        <li className="flex items-center gap-2">
          <span
            aria-hidden="true"
            className="inline-block h-0.5 w-6"
            style={{ backgroundColor: CHART_COLORS.shortWindow }}
          />
          {shortLabel}
        </li>
        <li className="flex items-center gap-2">
          {/* The legend swatch carries the same dash pattern as the line, so the
              two series stay tellable apart in words, in print, and in greyscale. */}
          <span
            aria-hidden="true"
            className="inline-block h-0 w-6 border-t-2 border-dashed"
            style={{ borderColor: CHART_COLORS.longWindow }}
          />
          {longLabel}
        </li>
      </ul>
    </div>
  );
}
