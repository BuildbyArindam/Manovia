import { describe, expect, it } from "vitest";

import { contrastRatio, parseHexColor, relativeLuminance } from "@/lib/contrast";

describe("contrast maths", () => {
  it("parses shorthand and full hex colours", () => {
    expect(parseHexColor("#fff")).toEqual([255, 255, 255]);
    expect(parseHexColor("#2E6A4E")).toEqual([46, 106, 78]);
  });

  it("rejects anything that is not a hex colour", () => {
    expect(() => parseHexColor("rebeccapurple")).toThrow(/hex colour/);
    expect(() => parseHexColor("#12345")).toThrow(/hex colour/);
  });

  it("matches the WCAG reference values", () => {
    expect(relativeLuminance("#ffffff")).toBeCloseTo(1, 5);
    expect(relativeLuminance("#000000")).toBeCloseTo(0, 5);
    expect(contrastRatio("#ffffff", "#000000")).toBeCloseTo(21, 5);
    expect(contrastRatio("#777777", "#ffffff")).toBeCloseTo(4.48, 1);
  });

  it("is symmetric and never below 1", () => {
    expect(contrastRatio("#2e6a4e", "#faf6ef")).toBeCloseTo(
      contrastRatio("#faf6ef", "#2e6a4e"),
      10,
    );
    expect(contrastRatio("#123456", "#123456")).toBe(1);
  });
});
