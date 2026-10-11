import { useCallback, useEffect, useReducer, useRef } from "react";

import { initialRunnerState, reduceRunner } from "./runner";
import type { ExerciseStep } from "./types";

/** No server clock or audio cue. A delayed/hidden page pauses, never skips holds. */
export function useExerciseTimer(steps: readonly ExerciseStep[]) {
  const [state, dispatch] = useReducer(
    (current: ReturnType<typeof initialRunnerState>, event: Parameters<typeof reduceRunner>[1]) =>
      reduceRunner(current, event, steps),
    steps,
    initialRunnerState,
  );
  const lastTick = useRef(0);

  const pause = useCallback((reason: "manual" | "background" | "delay" = "manual") => {
    const now = Date.now();
    const deltaMs = now - lastTick.current;
    // Account for the fraction since the last tick, but never silently skip
    // instructions after a suspended browser or a changed system clock.
    if (deltaMs >= 0 && deltaMs <= 2000) {
      dispatch({ type: "tick", deltaMs });
    }
    lastTick.current = now;
    dispatch({ type: "pause", reason });
  }, []);

  useEffect(() => {
    const visibility = (): void => {
      if (document.visibilityState === "hidden") {
        pause("background");
      }
    };
    document.addEventListener("visibilitychange", visibility);
    return () => document.removeEventListener("visibilitychange", visibility);
  }, [pause]);

  useEffect(() => {
    if (state.status !== "running") {
      return;
    }
    if (document.visibilityState === "hidden") {
      dispatch({ type: "pause", reason: "background" });
      return;
    }
    lastTick.current = Date.now();
    const interval = window.setInterval(() => {
      const now = Date.now();
      const deltaMs = now - lastTick.current;
      lastTick.current = now;
      if (document.visibilityState === "hidden") {
        dispatch({ type: "pause", reason: "background" });
      } else if (deltaMs < 0 || deltaMs > 2000) {
        dispatch({ type: "pause", reason: "delay" });
      } else {
        dispatch({ type: "tick", deltaMs });
      }
    }, 250);
    return () => window.clearInterval(interval);
  }, [state.status, steps]);

  return {
    state,
    secondsLeft: Math.ceil(state.remainingMs / 1000),
    start: (): void => dispatch({ type: "start" }),
    resume: (): void => dispatch({ type: "resume" }),
    pause,
  };
}
