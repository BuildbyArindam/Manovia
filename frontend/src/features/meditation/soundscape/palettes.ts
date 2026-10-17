/** Day 45: mood palettes for the adaptive soundscape.
 *
 * Every sound here is *procedural*: chords are oscillator frequencies, the
 * texture is a filtered noise buffer, and the motion is LFO-driven. There are
 * no audio samples anywhere in the repository, so nothing can carry a
 * copyright licence — the only "licence" in play is the code's own.
 *
 * A palette is a closed vocabulary of constants: the UI can only pick one of
 * these, and the engine can only work inside them (range, rate, depth),
 * which is what makes "changes are slow and bounded" a testable promise.
 */

export interface SoundscapePalette {
  id: string;
  /** Short, plain, non-judgemental name (localised in the UI, not here). */
  labelKey: string;
  /** The pad chord, in Hz. Low, few notes, slow — a bed, not a melody. */
  chord: readonly number[];
  /** The breathing pacer's resting tempo, breaths per minute (4-12). */
  defaultPaceBpm: number;
  /** The lowpass cutoff range the brightness may drift inside, in Hz. */
  cutoffRange: readonly [number, number];
  /** 0..1 — how much of the gentle noise texture the pad carries. */
  textureLevel: number;
  /** The centre of the texture's bandpass, in Hz. */
  textureCentre: number;
  /** 0..1 — how far the brightness follows each breath. */
  lfoDepth: number;
}

const TWO_SHARP_F = 369.99; // F#4
const A2 = 110.0;
const C3 = 130.81;
const D3 = 146.83;
const E3 = 164.81;
const G2 = 98.0;
const G3 = 196.0;
const A3 = 220.0;
const C2 = 65.41;

export const SOUNDSCAPE_PALETTES: readonly SoundscapePalette[] = [
  {
    id: "warm-dawn",
    labelKey: "meditation.palettes.warm-dawn",
    chord: [D3, A3, TWO_SHARP_F],
    defaultPaceBpm: 6,
    cutoffRange: [600, 1800],
    textureLevel: 0.25,
    textureCentre: 1400,
    lfoDepth: 0.35,
  },
  {
    id: "deep-forest",
    labelKey: "meditation.palettes.deep-forest",
    chord: [C2, G2, C3, E3],
    defaultPaceBpm: 5.5,
    cutoffRange: [300, 900],
    textureLevel: 0.15,
    textureCentre: 800,
    lfoDepth: 0.3,
  },
  {
    id: "soft-rain",
    labelKey: "meditation.palettes.soft-rain",
    chord: [A2, E3, A3],
    defaultPaceBpm: 6,
    cutoffRange: [500, 1400],
    textureLevel: 0.45,
    textureCentre: 2000,
    lfoDepth: 0.4,
  },
  {
    id: "night-calm",
    labelKey: "meditation.palettes.night-calm",
    chord: [G2, D3, G3],
    defaultPaceBpm: 5,
    cutoffRange: [250, 700],
    textureLevel: 0.12,
    textureCentre: 600,
    lfoDepth: 0.25,
  },
] as const;

export function paletteById(id: string): SoundscapePalette {
  const found = SOUNDSCAPE_PALETTES.find((palette) => palette.id === id);
  if (found === undefined) {
    throw new Error(`unknown soundscape palette: ${id}`);
  }
  return found;
}

/** Self-reported energy, closed vocabulary: low / normal / high. */
export type EnergyLevel = "low" | "normal" | "high";

/** Where the brightness rests for a given energy, inside the palette range.
 * Low energy leans toward the dark end, high toward the bright end; the
 * midpoint is the palette's resting point. */
export function brightnessForEnergy(
  range: readonly [number, number],
  energy: EnergyLevel,
): number {
  const [lo, hi] = range;
  switch (energy) {
    case "low":
      return lo + (hi - lo) * 0.25;
    case "high":
      return lo + (hi - lo) * 0.75;
    case "normal":
      return (lo + hi) / 2;
  }
}

/** Breaths per minute, clamped to the range the engine will ever honour. */
export const MIN_PACE_BPM = 4;
export const MAX_PACE_BPM = 12;
export function clampPace(bpm: number): number {
  return Math.min(MAX_PACE_BPM, Math.max(MIN_PACE_BPM, bpm));
}
