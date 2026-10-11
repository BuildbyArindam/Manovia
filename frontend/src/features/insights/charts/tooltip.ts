/**
 * Shared types for the chart tooltips.
 *
 * Recharts passes tooltip content an `active` flag and a `payload` array whose
 * entries carry the original datum plus the axis values. Typing it here (rather
 * than as `any`) means a renamed field in a chart's datum is a compile error at
 * the tooltip that renders it, which is the only place it is read.
 */

export interface TooltipEntry<TPoint> {
  /** The datum this entry came from. */
  payload: TPoint;
  /** Series name (`Valence`, `With tag`, …). */
  name?: string;
  /** The plotted value for this entry. */
  value?: number | null;
  /** The series colour. */
  color?: string;
}

export interface ChartTooltipProps<TPoint = Record<string, never>> {
  active?: boolean;
  payload?: TooltipEntry<TPoint>[] | undefined;
}
