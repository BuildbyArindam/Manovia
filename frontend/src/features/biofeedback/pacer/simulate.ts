/**
 * Deterministic simulated users for pacer stability tests (Day 42).
 *
 * A simulated user has a resonance rate `resonanceBpm`: the pace at which
 * their heart-rate oscillation is strongest. While the pacer breathes them at
 * `r`, their RR intervals carry a sinusoidal RSA component at `r` with
 * amplitude
 *
 *   A(r) = 3 + 55 * exp(-(r - resonanceBpm)^2 / (2 * 0.55^2))   [ms]
 *
 * plus small seeded noise. That is the smooth single-peak response the pacer's
 * walk is designed to climb; the test asserts the walk gets there, stays
 * inside the bounds, and settles rather than oscillates.
 *
 * Nothing here touches the network, a device or real physiology — it exists so
 * "pacer stability" is a reproducible number instead of a claim.
 */

import type { CleanSample } from "../signal/types";
import { BreathingPacer, DEFAULT_PACER_CONFIG, type PacerConfig } from "./pacer";

/** Small seeded LCG (same family as the fixture generator). */
class Lcg {
  private state: number;

  constructor(seed: number) {
    this.state = seed % 2147483647 || 1;
  }

  next(): number {
    this.state = (48271 * this.state) % 2147483647;
    return this.state / 2147483647;
  }

  normal(): number {
    const u1 = Math.max(this.next(), 1e-12);
    const u2 = this.next();
    return Math.sqrt(-2 * Math.log(u1)) * Math.cos(2 * Math.PI * u2);
  }
}

export interface SimulatedUser {
  resonanceBpm: number;
  /** ms of RSA amplitude at pace `r`. */
  amplitudeAt(rateBpm: number): number;
}

export function simulatedUser(resonanceBpm: number): SimulatedUser {
  return {
    resonanceBpm,
    amplitudeAt(rateBpm: number): number {
      const sigma = 0.55;
      const d = rateBpm - resonanceBpm;
      return 3 + 55 * Math.exp(-(d * d) / (2 * sigma * sigma));
    },
  };
}

export interface SessionPoint {
  atMs: number;
  rateBpm: number;
  amplitudeMs: number | null;
  decision: string;
}

export interface SimulatedSession {
  resonanceBpm: number;
  /** One point per adaptation (every `adaptIntervalMs`). */
  trajectory: SessionPoint[];
  /** Simulated RR samples as the session ran (timestamps + values). */
  sampleCount: number;
}

/**
 * Run one full simulated session: a pacer paced at its own current rate, and
 * a user whose heart oscillates at `user.amplitudeAt(pacedRate)` + noise.
 *
 * The beat generator runs on a continuous phase (so rate changes do not jump
 * the wave), at ~75 bpm base with 2.5 ms noise. The pacer adapts every
 * `adaptIntervalMs` on the clean series so far.
 */
export function simulateSession(
  user: SimulatedUser,
  options: {
    durationMs?: number;
    seed?: number;
    config?: Partial<PacerConfig>;
    noiseMs?: number;
  } = {},
): SimulatedSession {
  const {
    durationMs = 20 * 60_000,
    seed = 1234,
    config = {},
    noiseMs = 2.5,
  } = options;
  const pacer = new BreathingPacer(config);
  const rng = new Lcg(seed);
  const trajectory: SessionPoint[] = [];
  const samples: CleanSample[] = [];

  const stepMs = 1000;
  let nextBeatMs = 0;
  let phase = 0;
  let sampleCount = 0;

  for (let nowMs = 0; nowMs <= durationMs; nowMs += stepMs) {
    const rate = pacer.state().rateBpm;
    // Beats falling due in this second, under the paced oscillation.
    while (nextBeatMs <= nowMs) {
      const amp = user.amplitudeAt(rate);
      const rr = 800 + amp * Math.sin(phase) + noiseMs * rng.normal();
      nextBeatMs += rr;
      phase += (2 * Math.PI * rate) / 60 * (rr / 1000);
      samples.push({
        rrMs: Math.round(rr * 1000) / 1000,
        tMs: Math.round(nextBeatMs * 1000) / 1000,
        rawIndex: sampleCount,
        breakBefore: false,
      });
      sampleCount += 1;
    }
    pacer.advance(stepMs);

    const adaptEvery = config.adaptIntervalMs ?? DEFAULT_PACER_CONFIG.adaptIntervalMs;
    if (nowMs > 0 && nowMs % adaptEvery === 0) {
      const event = pacer.adapt(samples, "good", nowMs);
      trajectory.push({
        atMs: nowMs,
        rateBpm: event.toBpm,
        amplitudeMs: event.amplitudeMs,
        decision: event.decision,
      });
    }
  }

  return { resonanceBpm: user.resonanceBpm, trajectory, sampleCount };
}
