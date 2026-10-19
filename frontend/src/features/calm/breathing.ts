/**
 * The calm space's breathing cycle (Day 46).
 *
 * Four seconds in, six seconds out — six breaths a minute. That is the same
 * slow pace the biofeedback pacer settles at, but this module deliberately does
 * not depend on it: the calm space needs no sensor, no history and no network,
 * and a breathing cue must never fail because a heart-rate session cannot
 * start. What is shared is the *rule*: one continuous curve, no jumps, and a
 * visible word for the phase so the orb is never the only channel.
 *
 * `scale` is the orb's size multiplier, in [MIN_SCALE, 1]. It uses a raised
 * cosine over the full breath (not per phase), so the value at the end of the
 * exhale is exactly the value at the start of the next inhale. A visible jump
 * at a phase boundary would be a small flash, which is exactly what this
 * feature must not do.
 */

export interface BreathPattern {
  inhaleMs: number;
  exhaleMs: number;
}

export const CALM_BREATH: BreathPattern = { inhaleMs: 4000, exhaleMs: 6000 };
export const MIN_SCALE = 0.55;

export type BreathPhase = "inhale" | "exhale";

export interface BreathState {
  phase: BreathPhase;
  /** 0..1 within the current phase. */
  progress: number;
  /** 0..1 within the whole cycle, for a progress ring if one is ever added. */
  cycleProgress: number;
  /** Orb scale multiplier, in [MIN_SCALE, 1]. Continuous across phases. */
  scale: number;
  /** Complete cycles per minute. */
  rateBpm: number;
}

export function cycleLengthMs(pattern: BreathPattern = CALM_BREATH): number {
  return pattern.inhaleMs + pattern.exhaleMs;
}

export function breathAt(elapsedMs: number, pattern: BreathPattern = CALM_BREATH): BreathState {
  const cycle = cycleLengthMs(pattern);
  // Negative or non-finite elapsed time clamps to the start rather than
  // producing a NaN transform (a NaN scale blanks the canvas).
  const safeElapsed = Number.isFinite(elapsedMs) ? Math.max(0, elapsedMs) : 0;
  const inCycle = safeElapsed % cycle;
  const phase: BreathPhase = inCycle < pattern.inhaleMs ? "inhale" : "exhale";
  const progress =
    phase === "inhale"
      ? inCycle / Math.max(1, pattern.inhaleMs)
      : (inCycle - pattern.inhaleMs) / Math.max(1, pattern.exhaleMs);
  const cycleProgress = inCycle / cycle;
  // A single cosine over the whole cycle: zero slope at both ends, so there is
  // no velocity jump where the phases meet.
  const eased = 0.5 - 0.5 * Math.cos(cycleProgress * 2 * Math.PI);
  return {
    phase,
    progress,
    cycleProgress,
    scale: MIN_SCALE + (1 - MIN_SCALE) * eased,
    rateBpm: 60000 / cycle,
  };
}
