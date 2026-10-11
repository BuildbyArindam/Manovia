/**
 * The chart palette, and the two rules it is chosen under.
 *
 * **Rule one: no colour may rely on being seen.** Every pairing below is
 * accompanied by something other than hue — a dash pattern on the rolling
 * series, a direct label and a signed number on every bar, and a data table
 * behind each chart that holds the same numbers as text. That is WCAG 1.4.1
 * (use of colour), and it is also the only way a chart survives being read out
 * loud, printed in greyscale, or glanced at by somebody whose red-green channel
 * does not work the way the designer's did.
 *
 * **Rule two: the colours themselves are checked, not assumed.** They come from
 * the Okabe-Ito qualitative set, which was designed for colour-vision
 * deficiency, with one change: the members that fail WCAG 1.4.11 (non-text
 * contrast, 3:1) against this product's white surface were darkened until they
 * clear 3:1 against *both* the light and the dark surface. `palette.test.ts`
 * re-derives those numbers and also simulates all three dichromacies, so the
 * claim "colour-blind safe" is a test result rather than a citation.
 *
 * Because the Okabe-Ito hues cannot all be told apart by every viewer at once
 * (no eight-colour set can), only the small sets that ever appear *inside one
 * chart* are exported as pairs and trios. Those are the sets the test judges.
 */

export const CHART_COLORS = {
  /** The shorter rolling window (7 days): warm, solid line. */
  shortWindow: "#c97a00",
  /** The longer rolling window (30 days): cool, dashed line. */
  longWindow: "#0072b2",
  /** A mean below the comparison: warm-red, always drawn left of zero. */
  lower: "#d55e00",
  /** A mean above the comparison: blue, always drawn right of zero. */
  higher: "#0072b2",
  /** An ordinary bucket in a grouping. */
  bucket: "#2c8ca8",
  /** The two buckets a pattern sentence names. */
  named: "#c97a00",
  /** A bucket with too few check-ins to be named: present in the table, muted. */
  muted: "#7a6e5c",
  /** Grid and axis lines: the theme's own border, never a fourth data colour. */
  axis: "var(--manovia-border-strong)",
} as const;

/**
 * Dash patterns, keyed by the same roles as {@link CHART_COLORS}.
 *
 * `""` means solid. Two lines that differ in dash as well as hue are two lines
 * a deuteranope, a photocopier and a screen reader user can still tell apart.
 */
export const SERIES_STYLES = {
  shortWindow: "",
  longWindow: "7 5",
} as const;

/**
 * A dash pattern as Recharts wants it: `undefined` for a solid line.
 *
 * A helper rather than a ternary at the call site because the palette constants
 * are literal types, and comparing `"7 5" === ""` inline is a type error even
 * though it is exactly the question being asked.
 */
export function dashPattern(style: string): string | undefined {
  return style === "" ? undefined : style;
}

/** The two rolling series drawn in one line chart, in drawing order. */
export const ROLLING_PAIR = [CHART_COLORS.shortWindow, CHART_COLORS.longWindow] as const;

/** The two directions a difference bar can point in. */
export const DIRECTION_PAIR = [CHART_COLORS.lower, CHART_COLORS.higher] as const;

/** Ordinary bucket, named bucket, too-small bucket — one grouping chart. */
export const BUCKET_TRIO = [CHART_COLORS.bucket, CHART_COLORS.named, CHART_COLORS.muted] as const;

/**
 * Chart geometry.
 *
 * Widths are fixed rather than responsive on purpose. `ResponsiveContainer`
 * measures its parent, and in a jsdom test — and in any browser before layout
 * settles — that measurement is zero, which renders an empty chart and a test
 * that passes for the wrong reason. A fixed-width SVG inside a horizontally
 * scrollable region is honest in both places, and the scroll container is
 * `tabIndex={0}` with a label so a keyboard user can reach the overflow.
 */
export const CHART_GEOMETRY = {
  width: 640,
  height: 240,
  barHeight: 220,
  /** The check-in scale is 1-5; an axis that starts at zero flatters nothing. */
  domainMin: 1,
  domainMax: 5,
  margin: { top: 8, right: 16, bottom: 8, left: 0 },
} as const;

/** Colour for one bucket of a grouping, given which buckets a sentence names. */
export function bucketColor(
  bucketId: string,
  named: { lowest: string | null; highest: string | null },
  enoughData: boolean,
): string {
  if (!enoughData) {
    return CHART_COLORS.muted;
  }
  return bucketId === named.lowest || bucketId === named.highest
    ? CHART_COLORS.named
    : CHART_COLORS.bucket;
}

/** Colour for one difference bar: below the comparison, or above it. */
export function differenceColor(difference: number | null): string {
  if (difference === null) {
    return CHART_COLORS.muted;
  }
  return difference < 0 ? CHART_COLORS.lower : CHART_COLORS.higher;
}
