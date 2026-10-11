/**
 * Colour-vision-deficiency simulation and perceptual distance.
 *
 * A palette that looks distinct to the person who chose it is not a palette
 * that is distinct to everybody: roughly one man in twelve has a red-green
 * deficiency, and a chart that encodes "worse" and "better" in red and green is
 * the classic way to lose them. These two functions let a test ask the question
 * directly — *would these two colours still be different for a deuteranope?* —
 * instead of leaving it to a reviewer with good eyesight.
 *
 * The simulation is the Vienot/Brettel/Mollon linear-RGB approximation of
 * dichromacy: convert sRGB to linear light, apply one 3x3 matrix per deficiency,
 * convert back. It is an approximation of an approximation (real deficiency is a
 * spectrum, and this models the two extreme dichromacies), which is exactly why
 * the numbers it produces are used as a *floor* — a pair that survives this is
 * very likely to survive a milder anomalous trichromacy too.
 *
 * Distance is CIE76 (Euclidean in CIELAB). It is the crudest of the delta-E
 * family and overstates differences in the blue corner; for a "are these two
 * chart colours tellable apart" check that crudeness is acceptable, and the
 * threshold in `features/insights/palette.test.ts` is set generously to cover it.
 */

import { parseHexColor } from "./contrast";

/** The three dichromacies modelled here. */
export const VISION_TYPES = ["protanopia", "deuteranopia", "tritanopia"] as const;

export type VisionType = (typeof VISION_TYPES)[number];

type Matrix = readonly [
  readonly [number, number, number],
  readonly [number, number, number],
  readonly [number, number, number],
];

const MATRICES: Record<VisionType, Matrix> = {
  protanopia: [
    [0.11238, 0.88762, 0.0],
    [0.11238, 0.88762, 0.0],
    [0.00401, -0.00401, 1.0],
  ],
  deuteranopia: [
    [0.29275, 0.70725, 0.0],
    [0.29275, 0.70725, 0.0],
    [-0.02234, 0.02234, 1.0],
  ],
  tritanopia: [
    [1.0, 0.0, 0.0],
    [0.0, 1.0, 0.0],
    [-0.86744, 1.86744, 0.0],
  ],
};

/** D65 white point, for the sRGB -> XYZ step inside {@link lab}. */
const WHITE_POINT: Matrix[number] = [0.95047, 1.0, 1.08883];

function toLinear(channel8Bit: number): number {
  const channel = channel8Bit / 255;
  return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
}

function toSrgb(linear: number): number {
  const clamped = Math.min(1, Math.max(0, linear));
  const value = clamped <= 0.0031308 ? 12.92 * clamped : 1.055 * clamped ** (1 / 2.4) - 0.055;
  return Math.round(255 * value);
}

type Triple = [number, number, number];

/** A hex colour as linear-light RGB (0..1 per channel). */
export function linearRgb(hex: string): Triple {
  const [red, green, blue] = parseHexColor(hex);
  return [toLinear(red), toLinear(green), toLinear(blue)];
}

/** Linear-light RGB (0..1) back to a hex colour, clipped into gamut. */
export function hexFromLinear([red, green, blue]: Triple): string {
  const toByte = (value: number): string => toSrgb(value).toString(16).padStart(2, "0");
  return `#${toByte(red)}${toByte(green)}${toByte(blue)}`;
}

/**
 * What `hex` looks like to somebody with `vision`.
 *
 * The result is a real colour that can be dropped back into the same contrast
 * and distance helpers, which is what makes a palette test readable: simulate,
 * then measure, with no special-casing.
 */
export function simulateColorVision(hex: string, vision: VisionType): string {
  const [red, green, blue] = linearRgb(hex);
  const matrix = MATRICES[vision];
  return hexFromLinear([
    matrix[0][0] * red + matrix[0][1] * green + matrix[0][2] * blue,
    matrix[1][0] * red + matrix[1][1] * green + matrix[1][2] * blue,
    matrix[2][0] * red + matrix[2][1] * green + matrix[2][2] * blue,
  ]);
}

/** CIELAB coordinates of a hex colour (D65 white point). */
export function lab(hex: string): Triple {
  const [red, green, blue] = linearRgb(hex);
  const x = (0.4124 * red + 0.3576 * green + 0.1805 * blue) / WHITE_POINT[0];
  const y = 0.2126 * red + 0.7152 * green + 0.0722 * blue;
  const z = (0.0193 * red + 0.1192 * green + 0.9505 * blue) / WHITE_POINT[2];

  const pivot = (value: number): number =>
    value > 0.008856 ? value ** (1 / 3) : 7.787 * value + 16 / 116;
  const [fx, fy, fz] = [pivot(x), pivot(y), pivot(z)];

  return [116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)];
}

/** CIE76 perceptual distance. 0 is identical; above ~20 is clearly different. */
export function deltaE76(first: string, second: string): number {
  const [l1, a1, b1] = lab(first);
  const [l2, a2, b2] = lab(second);
  return Math.hypot(l1 - l2, a1 - a2, b1 - b2);
}

/**
 * The smallest distance between two colours across all three simulations, and
 * the deficiency that produced it.
 *
 * This is the number a palette is judged on: the *worst* viewer, not the
 * average one. A pair with a low minimum is a pair that somebody cannot tell
 * apart, and the answer says which somebody.
 */
export function worstSimulatedDistance(
  first: string,
  second: string,
): { vision: VisionType | "typical"; delta: number } {
  const candidates: { vision: VisionType | "typical"; delta: number }[] = [
    { vision: "typical", delta: deltaE76(first, second) },
  ];
  for (const vision of VISION_TYPES) {
    candidates.push({
      vision,
      delta: deltaE76(simulateColorVision(first, vision), simulateColorVision(second, vision)),
    });
  }
  return candidates.reduce((worst, candidate) =>
    candidate.delta < worst.delta ? candidate : worst,
  );
}
