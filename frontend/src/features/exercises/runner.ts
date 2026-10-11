/** Pure timer state machine. Durations come from the validated catalogue. */
import type { ExerciseStep } from "./types";

export interface RunnerState {
  status: "ready" | "running" | "paused" | "finished";
  stepIndex: number;
  remainingMs: number;
  elapsedMs: number;
  pauseReason: "manual" | "background" | "delay" | null;
}

export type RunnerEvent =
  | { type: "start" }
  | { type: "resume" }
  | { type: "pause"; reason: "manual" | "background" | "delay" }
  | { type: "tick"; deltaMs: number };

export function initialRunnerState(steps: readonly ExerciseStep[]): RunnerState {
  if (steps.length === 0) {
    throw new Error("An exercise needs at least one timed step");
  }
  return {
    status: "ready",
    stepIndex: 0,
    remainingMs: (steps[0]?.duration_seconds ?? 0) * 1000,
    elapsedMs: 0,
    pauseReason: null,
  };
}

export function reduceRunner(
  state: RunnerState,
  event: RunnerEvent,
  steps: readonly ExerciseStep[],
): RunnerState {
  if (event.type === "start") {
    return state.status === "ready" ? { ...state, status: "running" } : state;
  }
  if (event.type === "resume") {
    return state.status === "paused" ? { ...state, status: "running", pauseReason: null } : state;
  }
  if (event.type === "pause") {
    return state.status === "running"
      ? { ...state, status: "paused", pauseReason: event.reason }
      : state;
  }
  if (state.status !== "running" || event.deltaMs <= 0 || !Number.isFinite(event.deltaMs)) {
    return state;
  }

  let delta = event.deltaMs;
  let next = { ...state };
  while (delta > 0 && next.status === "running") {
    const consumed = Math.min(delta, next.remainingMs);
    next.remainingMs -= consumed;
    next.elapsedMs += consumed;
    delta -= consumed;
    if (next.remainingMs === 0) {
      const following = steps[next.stepIndex + 1];
      if (following === undefined) {
        next = { ...next, status: "finished" };
      } else {
        next = {
          ...next,
          stepIndex: next.stepIndex + 1,
          remainingMs: following.duration_seconds * 1000,
        };
      }
    }
  }
  return next;
}
