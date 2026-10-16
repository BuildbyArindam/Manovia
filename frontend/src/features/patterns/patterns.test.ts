import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import {
  aggregateDays,
  CAUTION,
  constructGraph,
  layout,
  patternSentence,
  TOO_SMALL,
  whatIf,
  withThemes,
} from "./model";
import { compare, seeded } from "./stats";
import { analyseExperiment, createExperiment, dateAt, experimentSentence } from "./experiment";
import { discoverThemes, mergeThemes } from "./themes";
import { EMPTY, exportSnapshot, parseSnapshot } from "./input";
import { synthetic } from "./fixtures";
import { wordingViolations } from "./wording";

describe("Day 16 browser statistics and graph", () => {
  it("withholds all distributions until BOTH groups have five days", () => {
    expect(compare([1, 2, 3, 4], [1, 2, 3, 4, 5], "a")).toBeNull();
    expect(compare([1, 2, 3, 4, 5], [1, 2, 3, 4], "b")).toBeNull();
    expect(whatIf(synthetic(1), "missing")).toBeNull();
    expect(compare([1, 1, 1, 1, 1], [1, 1, 1, 1, 1], "flat")).toMatchObject({
      d: null,
      p: 1,
      interval: [0, 0],
    });
  });
  it("recovers the known edges over 20 seeds; suppresses spurious edges", () => {
    let falseEdges = 0,
      recovered = 0;
    for (let seed = 0; seed < 20; seed++) {
      const graph = constructGraph(synthetic(seed));
      const ids = graph.edges.map((e) => e.factor.id);
      expect(ids).toContain("walk");
      expect(ids).toContain("sleep");
      recovered += ids.filter((id) => ["walk", "sleep"].includes(id)).length;
      falseEdges += ids.filter((id) => id.startsWith("noise")).length;
      if (seed === 0)
        console.warn(
          "Recovered edges seed 0:",
          graph.edges.map((e) => ({
            id: e.factor.id,
            difference: e.stats.difference,
            n: e.stats.n,
            ci: e.stats.interval,
            p: e.stats.p,
            d: e.stats.d,
          })),
        );
    }
    console.warn(
      `Known structure: ${recovered}/40 planted edges; ${falseEdges}/160 false edges (${((100 * falseEdges) / 160).toFixed(2)}%).`,
    );
    expect(falseEdges / 160).toBeLessThanOrEqual(0.05);
  });
  it("also checks 20 entirely null histories", () => {
    const count = Array.from(
      { length: 20 },
      (_, seed) => constructGraph(synthetic(seed, false)).edges.length,
    ).reduce((a, b) => a + b, 0);
    console.warn(`Pure noise graph: ${count}/200 false edges (${count / 2}%).`);
    expect(count / 200).toBeLessThanOrEqual(0.05);
  });
  it("aggregates repeated check-ins so sample sizes are days, not entries", () => {
    const day = synthetic(1)[0]!;
    expect(aggregateDays([day, { ...day, mood: 5 }])).toHaveLength(1);
    expect(aggregateDays([day, { ...day, mood: 5 }])[0]!.mood).toBe((day.mood + 5) / 2);
    expect(constructGraph(Array.from({ length: 100 }, () => day)).edges).toEqual([]);
  });
  it("has a deterministic bounded force layout without animation", () => {
    const graph = constructGraph(synthetic(0));
    const positions = layout(graph);
    expect(layout(graph)).toEqual(positions);
    expect(positions).toHaveLength(graph.nodes.length + 1);
    for (const p of positions) {
      expect(p.x).toBeGreaterThanOrEqual(30);
      expect(p.x).toBeLessThanOrEqual(570);
      expect(p.y).toBeGreaterThanOrEqual(30);
      expect(p.y).toBeLessThanOrEqual(370);
    }
  });
});

describe("local editable topic discovery", () => {
  it("clusters recurring vocabulary, never invents symptom names", () => {
    const ds = synthetic(2),
      themes = discoverThemes(ds);
    expect(themes).toHaveLength(2);
    expect(themes.map((t) => t.name)).toEqual(["Theme 1", "Theme 2"]);
    expect(themes[0]!.dates).toHaveLength(60);
    const named = themes.map((t, i) => ({ ...t, name: i === 0 ? "Outdoors" : "Books" }));
    expect(withThemes(ds, named)[0]!.factors.some((f) => f.label === "Outdoors")).toBe(true);
    const merged = mergeThemes(named, named[0]!.id, named[1]!.id);
    expect(merged).toHaveLength(1);
    expect(merged[0]!.dates).toHaveLength(120);
    expect(mergeThemes(named, named[0]!.id, named[0]!.id)).toEqual(named);
    expect(discoverThemes([])).toEqual([]);
  });
});

describe("two-week randomisation and analysis", () => {
  it("assigns exactly seven distinct days and is balanced across 1000 schedules", () => {
    const counts = Array<number>(14).fill(0);
    for (let seed = 0; seed < 1000; seed++) {
      const e = createExperiment("Walk", "2026-10-16", seeded(seed * 7919));
      expect(new Set(e.assigned).size).toBe(7);
      e.assigned.forEach((i) => {
        expect(i).toBeGreaterThanOrEqual(0);
        expect(i).toBeLessThan(14);
        counts[i]!++;
      });
    }
    for (const count of counts) {
      expect(count).toBeGreaterThan(420);
      expect(count).toBeLessThan(580);
    }
    console.warn("Assignment counts /1000:", counts);
  });
  it("refuses incomplete outcomes and invalid dates; survives month boundaries", () => {
    const e = createExperiment("Walk", "2026-10-25", seeded(1));
    expect(dateAt(e.start, 13)).toBe("2026-11-07");
    expect(analyseExperiment(e)).toBeNull();
    expect(() => createExperiment("Walk", "2026-02-30")).toThrow();
    expect(() => createExperiment("", "2026-10-16")).toThrow();
  });
  it("shows a true difference and a null result; measures 200-null false-positive rate", () => {
    const e = createExperiment("Walk", "2026-10-16", seeded(941));
    const signal = {
      ...e,
      moods: Object.fromEntries(
        Array.from({ length: 14 }, (_, i) => [
          dateAt(e.start, i),
          e.assigned.includes(i) ? 4.5 + (i % 2) * 0.2 : 2 + (i % 2) * 0.2,
        ]),
      ),
    };
    const trueResult = analyseExperiment(signal)!;
    console.warn("True experiment:", experimentSentence(trueResult));
    expect(trueResult.p).toBeLessThan(0.05);
    const flat = {
      ...e,
      moods: Object.fromEntries(Array.from({ length: 14 }, (_, i) => [dateAt(e.start, i), 3])),
    };
    console.warn("No-effect experiment:", experimentSentence(analyseExperiment(flat)));
    expect(analyseExperiment(flat)!.p).toBe(1);
    let positives = 0;
    for (let seed = 0; seed < 200; seed++) {
      const rng = seeded(seed * 7919 + 31),
        plan = createExperiment("Walk", "2026-10-16", rng);
      plan.moods = Object.fromEntries(
        Array.from({ length: 14 }, (_, i) => [dateAt(plan.start, i), 1 + 4 * rng()]),
      );
      if (analyseExperiment(plan)!.p <= 0.05) positives++;
    }
    console.warn(
      `Experiment null simulations: ${positives}/200 (${positives / 2}%) false positives; nominal alpha 5%.`,
    );
    // Predeclared finite Monte Carlo tolerance, not a claim of exact calibration.
    expect(positives / 200).toBeLessThanOrEqual(0.1);
  });
});

describe("local export and input boundary", () => {
  it("round-trips without raw journals; preserves experiment and themes", () => {
    const days = synthetic(4),
      themes = discoverThemes(days),
      experiment = createExperiment("Walk", "2026-10-16", seeded(2));
    const raw = exportSnapshot({ version: 1, days, themes, experiment });
    expect(raw).not.toContain("sunshine");
    expect(raw).not.toContain("journal");
    const restored = parseSnapshot(raw);
    expect(restored.experiment).toEqual(experiment);
    expect(restored.themes).toEqual(themes);
    expect(constructGraph(withThemes(restored.days, restored.themes))).toEqual(
      constructGraph(withThemes(days, themes)),
    );
    expect(parseSnapshot(exportSnapshot(EMPTY))).toEqual(EMPTY);
  });
  it("rejects invalid moods, reserved ids, oversized inputs and malformed plans", () => {
    const day = synthetic(4)[0]!;
    for (const mood of [0, 6, null, "3"])
      expect(() =>
        parseSnapshot(JSON.stringify({ version: 1, days: [{ ...day, mood }] })),
      ).toThrow();
    expect(() => parseSnapshot(" ".repeat(2_000_001))).toThrow();
    expect(() =>
      parseSnapshot(
        JSON.stringify({
          version: 1,
          days: [{ ...day, factors: [{ id: "mood", label: "x", kind: "factor" }] }],
        }),
      ),
    ).toThrow();
    expect(() =>
      parseSnapshot(
        JSON.stringify({
          ...EMPTY,
          experiment: {
            activity: "Walk",
            start: "2026-10-16",
            assigned: [1, 1, 2, 3, 4, 5, 6],
            moods: {},
          },
        }),
      ),
    ).toThrow();
  });
});

it("wording lint: app copy contains no causal or clinical promises (negative controls included)", () => {
  const copy = ["model.ts", "experiment.ts", "PatternsView.tsx"]
    .map((p) => readFileSync(new URL(p, import.meta.url), "utf8"))
    .join("\n");
  expect(wordingViolations(copy)).toEqual([]);
  for (const bad of [
    "Walking causes better mood",
    "Sleep improves mood",
    "This will reduce sadness",
    "This cures anxiety",
    "This leads to happiness",
  ])
    expect(wordingViolations(bad).length).toBeGreaterThan(0);
  for (const edge of constructGraph(synthetic(0)).edges)
    expect(wordingViolations(patternSentence(edge))).toEqual([]);
  expect(wordingViolations(CAUTION + " " + TOO_SMALL)).toEqual([]);
  console.warn(
    "Wording lint: 3 source files + pattern templates scanned; 0 violations; 5 negative controls rejected.",
  );
});
