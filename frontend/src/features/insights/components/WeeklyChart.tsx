/**
 * Week by week: two bars per week, mood and energy.
 *
 * Weeks with too few check-ins plot nothing rather than a zero-height bar, and a
 * week with none at all still occupies its slot on the axis. Both choices are the
 * same rule as everywhere else on this page — an absence of data is drawn as an
 * absence, never as a low number.
 *
 * The two series are the palette's most distant pair (a warm orange and a cool
 * blue, about 100 CIELAB units apart under every simulated deficiency), and the
 * legend names them, so the encoding does not rest on hue alone.
 */

import type { ReactElement } from "react";
import { Bar, BarChart, Legend, XAxis, YAxis } from "recharts";

import { usePrefersReducedMotion } from "@/lib/usePrefersReducedMotion";

import { CHART_COLORS, CHART_GEOMETRY } from "../palette";
import type { WeekRow } from "../series";

export interface WeeklyChartProps {
  rows: readonly WeekRow[];
  /** Localised labels for the two measures, from the response. */
  valenceLabel: string;
  energyLabel: string;
  scaleMax: number;
}

export function WeeklyChart({
  rows,
  valenceLabel,
  energyLabel,
  scaleMax,
}: WeeklyChartProps): ReactElement {
  const animate = !usePrefersReducedMotion();
  const data = rows.map((row) => ({
    label: row.label,
    valence: row.valence,
    energy: row.energy,
  }));

  return (
    <div data-testid="weekly-chart">
      <BarChart
        width={CHART_GEOMETRY.width}
        height={CHART_GEOMETRY.barHeight}
        data={data}
        margin={{ ...CHART_GEOMETRY.margin }}
      >
        <XAxis
          dataKey="label"
          interval={0}
          tick={{ fontSize: 11, fill: CHART_COLORS.axis }}
          stroke={CHART_COLORS.axis}
        />
        <YAxis
          domain={[0, scaleMax]}
          tick={{ fontSize: 12, fill: CHART_COLORS.axis }}
          stroke={CHART_COLORS.axis}
          width={32}
          tickCount={scaleMax + 1}
        />
        <Legend wrapperStyle={{ fontSize: 12 }} />
        <Bar
          dataKey="valence"
          name={valenceLabel}
          fill={CHART_COLORS.shortWindow}
          isAnimationActive={animate}
          barSize={16}
        />
        <Bar
          dataKey="energy"
          name={energyLabel}
          fill={CHART_COLORS.longWindow}
          isAnimationActive={animate}
          barSize={16}
        />
      </BarChart>
    </div>
  );
}
