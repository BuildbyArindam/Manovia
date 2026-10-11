/**
 * The palette, checked rather than trusted.
 *
 * Two claims are made about these colours and both are measured here:
 *
 * 1. **They are visible on this product's surfaces.** WCAG 1.4.11 wants 3:1 for a
 *    graphical object against its background, and the background is white in the
 *    light theme and near-black in the dark one. The surfaces are read out of
 *    `src/index.css` rather than repeated here, so changing a theme colour
 *    re-runs this check against the new value instead of against a stale copy.
 *    (This is why the Okabe-Ito set is not used verbatim: its yellow and sky blue
 *    are beautiful on white and invisible on it. They were darkened until both
 *    themes work.)
 * 2. **The colours that share a chart are tellable apart by everybody.** Not all
 *    eight of them — no qualitative set of eight survives dichromacy intact — but
 *    the small sets that are ever drawn together: the two rolling windows, the
 *    two directions a difference can point, the three states of a bucket. Each
 *    set is simulated under protanopia, deuteranopia and tritanopia and the
 *    *worst* distance in the set has to clear a generous threshold.
 */

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { contrastRatio } from "@/lib/contrast";
import { VISION_TYPES, worstSimulatedDistance } from "@/lib/colorVision";

import {
  BUCKET_TRIO,
  CHART_COLORS,
  DIRECTION_PAIR,
  ROLLING_PAIR,
  SERIES_STYLES,
  bucketColor,
  differenceColor,
} from "./palette";

const HERE = dirname(fileURLToPath(import.meta.url));
const CSS = readFileSync(join(HERE, "..", "..", "index.css"), "utf8");

/** Every distinct surface colour either theme can put behind a chart. */
function surfaces(): string[] {
  const found = [...CSS.matchAll(/--manovia-surface:\s*(#[0-9a-fA-F]{6})/g)].map(
    (match) => match[1] as string,
  );
  return [...new Set(found)];
}

/** The colours that carry data (the axis colour is a theme token, not data). */
function dataColors(): string[] {
  return Object.entries(CHART_COLORS)
    .filter(([role]) => role !== "axis")
    .map(([, value]) => value);
}

/** Every pairing that can appear inside one chart. */
const SETS = [
  { name: "the two rolling windows", colors: [...ROLLING_PAIR] },
  { name: "the two directions of a difference", colors: [...DIRECTION_PAIR] },
  { name: "the three states of a bucket", colors: [...BUCKET_TRIO] },
];

/**
 * The floor for "clearly different", in CIE76 units.
 *
 * 25 is generous on purpose: a just-noticeable difference is about 2, and a pair
 * that needs a legend and a squint sits around 10. The measured worst pair in
 * this palette is about 33, so this threshold fails only if a colour changes
 * into something genuinely confusable.
 */
const MIN_DISTANCE = 25;

describe("contrast on both themes", () => {
  const found = surfaces();

  it("reads the surfaces out of the stylesheet, not out of a copy", () => {
    expect(found.length).toBeGreaterThanOrEqual(2);
  });

  it.each(dataColors())("%s clears 3:1 against every surface", (color) => {
    for (const surface of found) {
      expect(contrastRatio(color, surface), `${color} on ${surface}`).toBeGreaterThanOrEqual(3);
    }
  });
});

describe("distinguishability under colour-vision deficiency", () => {
  it.each(SETS)("$name stays tellable apart for every simulated viewer", ({ colors }) => {
    for (let index = 0; index < colors.length; index += 1) {
      for (let other = index + 1; other < colors.length; other += 1) {
        const first = colors[index] as string;
        const second = colors[other] as string;
        const worst = worstSimulatedDistance(first, second);
        expect(
          worst.delta,
          `${first} vs ${second} collapses under ${worst.vision}`,
        ).toBeGreaterThanOrEqual(MIN_DISTANCE);
      }
    }
  });

  it("covers all three dichromacies, not only the common red-green pair", () => {
    expect([...VISION_TYPES]).toEqual(["protanopia", "deuteranopia", "tritanopia"]);
  });
});

describe("redundancy beyond colour", () => {
  it("gives the two rolling series different dash patterns as well as hues", () => {
    expect(SERIES_STYLES.shortWindow).not.toBe(SERIES_STYLES.longWindow);
    expect(SERIES_STYLES.shortWindow).toBe("");
  });

  it("colours a bucket by its role, and mutes the ones too small to name", () => {
    const named = { lowest: "night", highest: "evening" };

    expect(bucketColor("evening", named, true)).toBe(CHART_COLORS.named);
    expect(bucketColor("night", named, true)).toBe(CHART_COLORS.named);
    expect(bucketColor("morning", named, true)).toBe(CHART_COLORS.bucket);
    // A bucket with too few check-ins is muted even if a sentence named it: the
    // server cannot name one, and the colour should not imply that it could.
    expect(bucketColor("evening", named, false)).toBe(CHART_COLORS.muted);
  });

  it("colours a difference by its sign, and mutes one that has no value", () => {
    expect(differenceColor(-0.85)).toBe(CHART_COLORS.lower);
    expect(differenceColor(0.7)).toBe(CHART_COLORS.higher);
    expect(differenceColor(null)).toBe(CHART_COLORS.muted);
  });
});
