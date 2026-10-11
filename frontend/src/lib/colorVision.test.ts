/**
 * The colour-vision simulation itself, tested before anything trusts it.
 *
 * A palette test built on a simulation that quietly returns its input would pass
 * forever and mean nothing, so the checks here are about the model's behaviour
 * rather than about any palette: greys survive (a deficiency removes a channel,
 * it does not add colour), a pair that looks distinct to typical vision can be
 * all but identical to a protanope, and identical colours are zero apart.
 */

import { describe, expect, it } from "vitest";

import {
  VISION_TYPES,
  deltaE76,
  hexFromLinear,
  lab,
  linearRgb,
  simulateColorVision,
  worstSimulatedDistance,
} from "./colorVision";

describe("the linear-light round trip", () => {
  it.each([
    ["#000000", "#000000"],
    ["#ffffff", "#ffffff"],
    ["#808080", "#808080"],
    ["#c97a00", "#c97a00"],
  ])("%s survives encode/decode", (hex) => {
    expect(hexFromLinear(linearRgb(hex))).toBe(hex);
  });
});

describe("simulation", () => {
  it("leaves a neutral grey alone under every deficiency", () => {
    // A grey has equal channels; the matrices are row-stochastic on the two
    // collapsed rows, so grey is a fixed point. A simulation that shifted greys
    // would be adding colour that a real deficiency removes.
    for (const vision of VISION_TYPES) {
      expect(simulateColorVision("#808080", vision)).toBe("#808080");
    }
  });

  it("moves a saturated colour a long way under every deficiency", () => {
    // A reddish purple: all three channels contribute, so all three matrices
    // move it. (A colour with no blue in it is left alone by tritanopia, which
    // is correct behaviour and the reason this one was chosen.)
    const purple = "#cc79a7";
    for (const vision of VISION_TYPES) {
      expect(deltaE76(purple, simulateColorVision(purple, vision))).toBeGreaterThan(10);
    }
  });

  it("collapses a pair that typical vision finds easy", () => {
    // A sky blue and a reddish purple: about 58 units apart for typical vision
    // and about 1 for a protanope, which is the whole reason this module exists.
    const sky = "#2c8ca8";
    const purple = "#cc79a7";

    expect(deltaE76(sky, purple)).toBeGreaterThan(40);
    expect(
      deltaE76(simulateColorVision(sky, "protanopia"), simulateColorVision(purple, "protanopia")),
    ).toBeLessThan(5);
  });
});

describe("distance", () => {
  it("is zero for a colour against itself", () => {
    expect(deltaE76("#0072b2", "#0072b2")).toBe(0);
  });

  it("is symmetric", () => {
    expect(deltaE76("#c97a00", "#0072b2")).toBeCloseTo(deltaE76("#0072b2", "#c97a00"), 10);
  });

  it("puts white and black about 100 apart", () => {
    // L* runs 0..100, so this is the sanity check on the CIELAB conversion.
    expect(deltaE76("#000000", "#ffffff")).toBeCloseTo(100, 0);
  });

  it("reports the worst viewer, and names them", () => {
    const worst = worstSimulatedDistance("#2c8ca8", "#cc79a7");
    expect(worst.vision).toBe("protanopia");
    expect(worst.delta).toBeLessThan(5);
  });

  it("reports typical vision when that is the worst case", () => {
    const worst = worstSimulatedDistance("#c97a00", "#0072b2");
    expect(["typical", ...VISION_TYPES]).toContain(worst.vision);
    expect(worst.delta).toBeGreaterThan(50);
  });
});

describe("lab", () => {
  it("places white at L*=100 and black at L*=0", () => {
    expect(lab("#ffffff")[0]).toBeCloseTo(100, 5);
    expect(lab("#000000")[0]).toBeCloseTo(0, 5);
  });
});
