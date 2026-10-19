/**
 * Sensory profile (Day 46).
 *
 * One small set of choices, applied app-wide through the same design tokens and
 * data attributes the rest of the interface already uses. Nothing here is
 * stored on the server: the profile is a display preference, and a display
 * preference is not a health record.
 *
 * The vocabulary is deliberately tiny. "Motion: low" is not a promise to remove
 * every pixel of movement — it means no decorative loop, no parallax, no
 * auto-playing drift; "none" means the interface holds still. The honest
 * definitions are written in `SENSORY_COPY` and shown next to each control.
 */

export type MotionLevel = "none" | "low" | "normal";
export type SoundLevel = "off" | "low";
export type DensityLevel = "simple" | "standard";
export type ColourMode = "calm" | "high-contrast";
export type FontSize = "normal" | "large" | "larger";

export interface SensoryProfile {
  motion: MotionLevel;
  sound: SoundLevel;
  density: DensityLevel;
  colours: ColourMode;
  fontSize: FontSize;
}

/** Stored shape version. A future change bumps it rather than guessing. */
export const SENSORY_PROFILE_VERSION = 1;

export const SENSORY_STORAGE_KEY = "manovia.sensory-profile.v1";
/** Overwhelmed mode lasts for the tab, not forever. */
export const OVERWHELMED_STORAGE_KEY = "manovia.overwhelmed.v1";

export const DEFAULT_SENSORY_PROFILE: SensoryProfile = {
  motion: "normal",
  sound: "low",
  density: "standard",
  colours: "calm",
  fontSize: "normal",
};

export const MOTION_LEVELS: readonly MotionLevel[] = ["normal", "low", "none"];
export const SOUND_LEVELS: readonly SoundLevel[] = ["low", "off"];
export const DENSITY_LEVELS: readonly DensityLevel[] = ["standard", "simple"];
export const COLOUR_MODES: readonly ColourMode[] = ["calm", "high-contrast"];
export const FONT_SIZES: readonly FontSize[] = ["normal", "large", "larger"];

/** Multiplier applied to the 16px base. Never below 1: text never shrinks. */
export const FONT_SCALE: Readonly<Record<FontSize, number>> = {
  normal: 1,
  large: 1.125,
  larger: 1.25,
};

export interface Option<T extends string> {
  value: T;
  labelKey: string;
  hintKey: string;
}

function options<T extends string>(values: readonly T[], prefix: string): ReadonlyArray<Option<T>> {
  return values.map((value) => ({
    value,
    labelKey: `${prefix}.${value}.label`,
    hintKey: `${prefix}.${value}.hint`,
  }));
}

export const MOTION_OPTIONS = options(MOTION_LEVELS, "sensory.motion");
export const SOUND_OPTIONS = options(SOUND_LEVELS, "sensory.sound");
export const DENSITY_OPTIONS = options(DENSITY_LEVELS, "sensory.density");
export const COLOUR_OPTIONS = options(COLOUR_MODES, "sensory.colours");
export const FONT_OPTIONS = options(FONT_SIZES, "sensory.fontSize");

/** True when `value` is one of the profile's allowed values. */
export function isMotionLevel(value: unknown): value is MotionLevel {
  return value === "none" || value === "low" || value === "normal";
}

export function isSoundLevel(value: unknown): value is SoundLevel {
  return value === "off" || value === "low";
}

export function isDensityLevel(value: unknown): value is DensityLevel {
  return value === "simple" || value === "standard";
}

export function isColourMode(value: unknown): value is ColourMode {
  return value === "calm" || value === "high-contrast";
}

export function isFontSize(value: unknown): value is FontSize {
  return value === "normal" || value === "large" || value === "larger";
}
