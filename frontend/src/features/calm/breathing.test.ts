import { describe, expect, it } from "vitest";

import { CALM_BREATH, MIN_SCALE, breathAt, cycleLengthMs } from "./breathing";

describe("the calm breathing curve", () => {
  it("is six breaths a minute", () => {
    expect(cycleLengthMs(CALM_BREATH)).toBe(10_000);
    expect(breathAt(0).rateBpm).toBeCloseTo(6, 5);
  });

  it("switches phase at four seconds and stays inside its bounds", () => {
    expect(breathAt(0).phase).toBe("inhale");
    expect(breathAt(3999).phase).toBe("inhale");
    expect(breathAt(4000).phase).toBe("exhale");
    for (let ms = 0; ms <= 30_000; ms += 37) {
      const state = breathAt(ms);
      expect(state.scale).toBeGreaterThanOrEqual(MIN_SCALE - 1e-9);
      expect(state.scale).toBeLessThanOrEqual(1 + 1e-9);
      expect(state.progress).toBeGreaterThanOrEqual(0);
      expect(state.progress).toBeLessThanOrEqual(1);
    }
  });

  it("has no jump where the phases meet", () => {
    const before = breathAt(3999.99);
    const after = breathAt(4000.01);
    expect(Math.abs(after.scale - before.scale)).toBeLessThan(0.001);
    // The whole cycle is continuous, including the wrap to the next inhale.
    const end = breathAt(9999.99);
    const start = breathAt(10000.01);
    expect(Math.abs(end.scale - start.scale)).toBeLessThan(0.001);
  });

  it("changes slowly enough that no frame is a pulse", () => {
    let largest = 0;
    let previous = breathAt(0).scale;
    for (let ms = 16; ms <= 10_000; ms += 16) {
      const current = breathAt(ms).scale;
      largest = Math.max(largest, Math.abs(current - previous));
      previous = current;
    }
    // At 60 fps the largest step is ~0.3% of the orb's full range.
    expect(largest).toBeLessThan(0.005);
  });

  it("clamps nonsense instead of producing NaN", () => {
    expect(breathAt(-5000).phase).toBe("inhale");
    expect(Number.isFinite(breathAt(Number.NaN).scale)).toBe(true);
    expect(Number.isFinite(breathAt(Number.POSITIVE_INFINITY).scale)).toBe(true);
  });
});
