/**
 * Signal quality indicator (Day 42).
 *
 * The rule the whole feature hangs on: **refuse to show numbers when quality
 * is poor.** A missing RMSSD is honest; a wrong one is a confidently wrong
 * number about someone's body. The refusal is enforced in `metrics.ts` (the
 * value is null) and surfaced in the UI with the reason it is withheld.
 *
 * The score is a weighted combination of what the window actually contained:
 *
 *   score = 0.65 * artefactScore + 0.35 * gapScore
 *
 * where `artefactScore` is the fraction of beats that survived rejection and
 * `gapScore` is the fraction of the window not swallowed by dropouts. Three
 * hard overrides cap the score regardless of the combination:
 *
 *   - the sensor reports "contact not detected" (probe lifted, strap loose)
 *     -> the score is capped at 0.35;
 *   - more than 80% of successive pairs are within 1 ms of each other
 *     ("flatline": a sensor repeating one value) -> the score is capped at
 *     0.35, because a flat series would otherwise produce a confidently
 *     tiny RMSSD from sensor failure rather than from a calm heartbeat;
 *   - fewer than `minBeats` accepted intervals -> level "poor" outright
 *     (nothing sensible can be computed from almost no beats).
 *
 * Levels: good >= 0.85, fair >= 0.65, poor < 0.65. All three thresholds are
 * pinned by unit tests and documented in docs/adr/0041-biofeedback-hrv.md.
 *
 * WELLNESS FEATURE. Not a medical device; quality here is about trusting the
 * pacer's numbers, not about clinical signal fidelity.
 */

import type { QualityLevel } from "./types";

export interface QualityInputs {
  acceptedBeats: number;
  rejectedBeats: number;
  /** Gap time inside the window, ms. */
  gapMs: number;
  /** Window length, ms. */
  windowMs: number;
  /** From the sensor, when it reports contact. Null when unknown/unsupported. */
  contactDetected: boolean | null;
  /** Minimum accepted beats before any metric is shown. */
  minBeats: number;
  /**
   * Fraction of successive accepted pairs whose RR values differ by less than
   * 1 ms. Above 0.8 the sensor is repeating one value ("flatline").
   */
  flatlineFraction?: number;
}

/** Score at or above this is "good". */
export const QUALITY_GOOD = 0.85;
/** Score at or above this is "fair" (numbers shown). Below: "poor" (withheld). */
export const QUALITY_FAIR = 0.65;
/** Cap applied when contact is lost or the sensor output is flat. */
export const CONTACT_LOSS_CAP = 0.35;
/** Successive pairs flatter than this fraction cap the score (flatline). */
export const FLATLINE_CAP_FRACTION = 0.8;

export function qualityScore(inputs: QualityInputs): number {
  const total = inputs.acceptedBeats + inputs.rejectedBeats;
  const artefactScore = total === 0 ? 0 : inputs.acceptedBeats / total;
  const gapScore =
    inputs.windowMs <= 0 ? 0 : 1 - Math.min(1, Math.max(0, inputs.gapMs / inputs.windowMs));
  let score = 0.65 * artefactScore + 0.35 * gapScore;
  if (inputs.contactDetected === false) {
    score = Math.min(score, CONTACT_LOSS_CAP);
  }
  if ((inputs.flatlineFraction ?? 0) > FLATLINE_CAP_FRACTION) {
    score = Math.min(score, CONTACT_LOSS_CAP);
  }
  return Math.min(1, Math.max(0, score));
}

export function qualityLevel(inputs: QualityInputs): QualityLevel {
  if (inputs.acceptedBeats < inputs.minBeats) {
    return "poor";
  }
  const score = qualityScore(inputs);
  if (score >= QUALITY_GOOD) {
    return "good";
  }
  if (score >= QUALITY_FAIR) {
    return "fair";
  }
  return "poor";
}

/** The level and its score together, the way every consumer uses them. */
export function assessQuality(inputs: QualityInputs): {
  level: QualityLevel;
  score: number;
} {
  return { level: qualityLevel(inputs), score: qualityScore(inputs) };
}
