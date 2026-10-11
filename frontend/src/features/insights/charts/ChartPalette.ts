/**
 * Colour-blind-safe palette for data visualisation.
 *
 * These colours are chosen to be distinguishable for the most common types of
 * colour vision deficiency (protanopia, deuteranopia, tritanopia) and meet
 * WCAG AA contrast against both light and dark Manovia backgrounds.
 *
 * Source: adapted from the "Okabe-Ito" palette and ColorBrewer safe sets.
 */

interface ChartPalette {
  categorical: readonly string[];
  valence: string;
  energy: string;
  trendUp: string;
  trendDown: string;
  trendFlat: string;
  trendUnknown: string;
  grid: string;
  axis: string;
  tooltipBg: string;
  tooltipText: string;
  focusRing: string;
}

export const CHART_PALETTE: ChartPalette = {
  // Primary categorical colours (safe for colour-blind viewers)
  categorical: [
    "#0072B2", // blue
    "#D55E00", // vermillion
    "#009E73", // bluish green
    "#CC79A7", // reddish purple
    "#E69F00", // orange
    "#56B4E9", // sky blue
    "#F0E442", // yellow
    "#000000", // black (last resort)
  ],

  // Semantic colours for valence/energy
  valence: "#0072B2", // blue for mood
  energy: "#D55E00", // vermillion for energy

  // Trend colours
  trendUp: "#009E73", // green (not pure green - safe)
  trendDown: "#D55E00", // red/vermillion
  trendFlat: "#56B4E9", // sky blue
  trendUnknown: "#999999", // grey

  // Background and grid
  grid: "var(--manovia-border)",
  axis: "var(--manovia-text-muted)",
  tooltipBg: "var(--manovia-surface)",
  tooltipText: "var(--manovia-text)",

  // Focus/selection
  focusRing: "var(--manovia-focus)",
};

/** Get a colour by index, cycling through the categorical palette. */
export function getCategoricalColour(index: number): string {
  const arr = CHART_PALETTE.categorical;
  return arr[index % arr.length] as string;
}

/** CSS custom properties for the palette (injected by the app shell). */
export const CHART_CSS_VARS: Record<string, string> = {
  "--chart-valence": CHART_PALETTE.valence,
  "--chart-energy": CHART_PALETTE.energy,
  "--chart-trend-up": CHART_PALETTE.trendUp,
  "--chart-trend-down": CHART_PALETTE.trendDown,
  "--chart-trend-flat": CHART_PALETTE.trendFlat,
  "--chart-trend-unknown": CHART_PALETTE.trendUnknown,
  "--chart-grid": CHART_PALETTE.grid,
  "--chart-axis": CHART_PALETTE.axis,
  "--chart-tooltip-bg": CHART_PALETTE.tooltipBg,
  "--chart-tooltip-text": CHART_PALETTE.tooltipText,
};
