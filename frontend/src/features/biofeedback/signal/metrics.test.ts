/**
 * Metric tests with hand-calculated values (Day 42): RMSSD against known
 * answers, mean HR, windowing, and the quality gate that withholds numbers.
 */

import { describe, expect, it } from "vitest";

import { rejectArtefacts } from "./artefact";
import {
  DEFAULT_WINDOW_OPTIONS,
  flatlineFraction,
  meanHeartRateBpm,
  rmssdMs,
  slidingWindowMetrics,
} from "./metrics";
import { assessQuality, qualityLevel, qualityScore } from "./quality";
import type { RRSample } from "./types";

function samples(values: number[], startMs = 0): RRSample[] {
  let t = startMs;
  return values.map((rrMs) => {
    t += rrMs;
    return { rrMs, tMs: t };
  });
}

describe("rmssdMs against known values", () => {
  it("is zero for a metronome", () => {
    const s = rejectArtefacts(samples([800, 800, 800, 800, 800, 800, 800, 800, 800, 800, 800]));
    expect(rmssdMs(s.accepted, 10)).toBeCloseTo(0, 12);
  });

  it("is exactly 20 for a 800/820 alternation", () => {
    // diffs: +20, -20, +20 -> mean square 400 -> sqrt = 20.
    const s = rejectArtefacts(
      samples([800, 820, 800, 820, 800, 820, 800, 820, 800, 820, 800]),
    );
    expect(rmssdMs(s.accepted, 10)).toBeCloseTo(20, 12);
  });

  it("matches a hand computation on four successive differences", () => {
    // diffs: 10, -20, 30, -40 -> squares 100, 400, 900, 1600 -> mean 750
    // -> sqrt(750) = 27.386127875258307
    const s = rejectArtefacts(samples([800, 810, 790, 820, 780]));
    expect(rmssdMs(s.accepted, 4)).toBeCloseTo(Math.sqrt(750), 12);
  });

  it("skips pairs that cross an artefact break", () => {
    // 620 and 980 are removed, so the 800 -> 805 difference that spans them is
    // not a successive difference and must not be counted. The remaining
    // differences are -10, +5, +10, -10, -5, +10, -5, 0 (8 pairs).
    const raw = samples([800, 620, 980, 805, 795, 800, 810, 800, 795, 805, 800, 800]);
    const s = rejectArtefacts(raw);
    const expected = Math.sqrt((100 + 25 + 100 + 100 + 25 + 100 + 25 + 0) / 8);
    expect(rmssdMs(s.accepted, 7)).toBeCloseTo(expected, 12);
  });

  it("returns null below the minimum pair count", () => {
    const s = rejectArtefacts(samples([800, 820, 800]));
    expect(rmssdMs(s.accepted, 10)).toBeNull();
    expect(rmssdMs(s.accepted, 2)).toBeCloseTo(20, 12);
  });
});

describe("meanHeartRateBpm", () => {
  it("is 60000/mean RR", () => {
    expect(meanHeartRateBpm([{ rrMs: 1000 }, { rrMs: 1000 }])).toBeCloseTo(60, 12);
    expect(meanHeartRateBpm([{ rrMs: 800 }, { rrMs: 800 }])).toBeCloseTo(75, 12);
    expect(meanHeartRateBpm([{ rrMs: 800 }, { rrMs: 1000 }])).toBeCloseTo(60_000 / 900, 10);
    expect(meanHeartRateBpm([])).toBeNull();
  });
});

describe("flatlineFraction", () => {
  it("detects a sensor repeating one value", () => {
    const flat = rejectArtefacts(samples([800, 800, 800, 800, 800, 800, 800, 800, 800, 800, 800]));
    expect(flatlineFraction(flat.accepted)).toBe(1);
    const varied = rejectArtefacts(samples([800, 820, 800, 820, 800, 820, 800, 820, 800, 820, 800]));
    expect(flatlineFraction(varied.accepted)).toBe(0);
  });
});

describe("quality gate", () => {
  const base = {
    acceptedBeats: 60,
    rejectedBeats: 0,
    gapMs: 0,
    windowMs: 60_000,
    contactDetected: null,
    minBeats: 15,
  };

  it("rates a clean window good and shows numbers", () => {
    expect(qualityScore(base)).toBeCloseTo(1, 12);
    expect(qualityLevel(base)).toBe("good");
  });

  it("drops to fair with moderate artefacts", () => {
    const inputs = { ...base, acceptedBeats: 45, rejectedBeats: 15 };
    // 45/60 = 0.75 -> 0.65 * 0.75 + 0.35 = 0.8375 -> fair (0.65 <= x < 0.85)
    expect(qualityScore(inputs)).toBeCloseTo(0.8375, 12);
    expect(qualityLevel(inputs)).toBe("fair");
  });

  it("goes poor and withholds when artefacts dominate", () => {
    const inputs = { ...base, acceptedBeats: 25, rejectedBeats: 35 };
    expect(qualityLevel(inputs)).toBe("poor");
  });

  it("caps at 0.35 when contact is not detected", () => {
    const inputs = { ...base, contactDetected: false };
    expect(qualityScore(inputs)).toBe(0.35);
    expect(qualityLevel(inputs)).toBe("poor");
  });

  it("caps at 0.35 when the sensor output is flat", () => {
    const inputs = { ...base, flatlineFraction: 0.95 };
    expect(qualityScore(inputs)).toBe(0.35);
    expect(qualityLevel(inputs)).toBe("poor");
  });

  it("is poor outright below the minimum beats", () => {
    const inputs = { ...base, acceptedBeats: 10 };
    expect(assessQuality(inputs).level).toBe("poor");
    // The score can be high (perfect signal) and still not be shown.
    expect(assessQuality(inputs).score).toBe(1);
  });
});

describe("slidingWindowMetrics", () => {
  it("computes 60 s windows stepping 30 s over a clean minute", () => {
    // 100 beats of 600 ms = 60 s of data at 100 bpm.
    const raw = samples(Array.from({ length: 100 }, (_, i) => (i % 2 === 0 ? 600 : 620)));
    const s = rejectArtefacts(raw);
    const windows = slidingWindowMetrics(s, { minBeats: 15, minSuccessivePairs: 10 });
    expect(windows.length).toBeGreaterThan(1);
    const full = windows.find((w) => w.acceptedBeats >= 90);
    expect(full?.quality).toBe("good");
    expect(full?.rmssdMs).toBeCloseTo(20, 6);
    expect(full?.meanHrBpm).toBeCloseTo(60_000 / 610, 4);
  });

  it("withholds numbers (null) in poor windows, not just hides them in the UI", () => {
    // A minute of dense ectopics: every third beat is wrong.
    const values: number[] = [];
    for (let i = 0; i < 120; i += 1) {
      values.push(i % 3 === 0 ? 1000 : i % 3 === 1 ? 600 : 800);
    }
    const s = rejectArtefacts(samples(values));
    const windows = slidingWindowMetrics(s, { minBeats: 15, minSuccessivePairs: 10 });
    const poor = windows.filter((w) => w.quality === "poor");
    expect(poor.length).toBeGreaterThan(0);
    for (const w of poor) {
      expect(w.rmssdMs).toBeNull();
      expect(w.meanHrBpm).toBeNull();
    }
    // Nothing anywhere shows a number while quality is poor.
    for (const w of windows) {
      if (w.quality === "poor") {
        expect(w.rmssdMs).toBeNull();
      }
    }
  });

  it("returns nothing for an empty series", () => {
    const s = rejectArtefacts([]);
    expect(slidingWindowMetrics(s)).toEqual([]);
  });

  it("uses the documented defaults", () => {
    expect(DEFAULT_WINDOW_OPTIONS.windowMs).toBe(60_000);
    expect(DEFAULT_WINDOW_OPTIONS.stepMs).toBe(30_000);
    expect(DEFAULT_WINDOW_OPTIONS.minBeats).toBe(15);
    expect(DEFAULT_WINDOW_OPTIONS.minSuccessivePairs).toBe(10);
  });
});
