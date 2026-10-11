import { describe, expect, it } from "vitest";

import { breathingExercise } from "@/test/exerciseFakes";

import { initialRunnerState, reduceRunner } from "./runner";

const steps = breathingExercise().steps;

describe("exercise timer state machine", () => {
  it("does not tick until started, or while paused", () => {
    const ready = initialRunnerState(steps);
    expect(reduceRunner(ready, { type: "tick", deltaMs: 1000 }, steps)).toBe(ready);
    const running = reduceRunner(ready, { type: "start" }, steps);
    const paused = reduceRunner(running, { type: "pause", reason: "manual" }, steps);
    expect(reduceRunner(paused, { type: "tick", deltaMs: 10000 }, steps)).toBe(paused);
    expect(reduceRunner(paused, { type: "start" }, steps)).toBe(paused);
    expect(reduceRunner(paused, { type: "resume" }, steps).status).toBe("running");
  });

  it("consumes exact boundaries and clamps completion at the final duration", () => {
    const running = reduceRunner(initialRunnerState(steps), { type: "start" }, steps);
    const before = reduceRunner(running, { type: "tick", deltaMs: 3999 }, steps);
    expect(before.stepIndex).toBe(0);
    expect(before.remainingMs).toBe(1);
    const boundary = reduceRunner(before, { type: "tick", deltaMs: 1 }, steps);
    expect(boundary.stepIndex).toBe(1);
    expect(boundary.remainingMs).toBe(4000);
    const finished = reduceRunner(boundary, { type: "tick", deltaMs: 100000 }, steps);
    expect(finished).toMatchObject({
      status: "finished",
      elapsedMs: 12000,
      remainingMs: 0,
      stepIndex: 2,
    });
    expect(reduceRunner(finished, { type: "resume" }, steps)).toBe(finished);
  });

  it.each([0, -1, Number.NaN, Number.POSITIVE_INFINITY])(
    "ignores invalid elapsed time %s",
    (deltaMs) => {
      const running = reduceRunner(initialRunnerState(steps), { type: "start" }, steps);
      expect(reduceRunner(running, { type: "tick", deltaMs }, steps)).toBe(running);
    },
  );

  it("rejects an empty step list", () => {
    expect(() => initialRunnerState([])).toThrow("at least one timed step");
  });
});
