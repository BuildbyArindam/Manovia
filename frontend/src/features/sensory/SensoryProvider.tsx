/**
 * Sensory profile provider (Day 46).
 *
 * Applies the reader's choices as data attributes on `<html>`, which is how the
 * rest of the app already switches palettes. That has three consequences worth
 * stating:
 *
 * 1. Every surface follows the profile without importing anything, including
 *    CSS-only animations and `prefers-reduced-motion` rules.
 * 2. A change is immediate: one attribute write re-paints the app.
 * 3. The effective values are observable in tests (`data-sensory-motion`,
 *    `data-overwhelmed`, ...), instead of being buried in a class name.
 *
 * The provider never overrides the operating system's reduced-motion request:
 * if the OS asks for less motion, effective motion is `none` whatever the
 * profile says. `data-sensory-motion` is the *effective* value;
 * `data-sensory-motion-choice` is what the person picked, so Settings can show
 * both honestly.
 *
 * Overwhelmed mode is a session state, not a profile change. It forces motion
 * `none`, sound `off` and density `simple` while it is on, leaving the stored
 * profile untouched so leaving the mode restores exactly what was there.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactElement,
  type ReactNode,
} from "react";

import { usePrefersReducedMotion } from "@/lib/usePrefersReducedMotion";

import {
  readOverwhelmed,
  readSensoryProfile,
  writeOverwhelmed,
  writeSensoryProfile,
} from "./storage";
import {
  DEFAULT_SENSORY_PROFILE,
  FONT_SCALE,
  type DensityLevel,
  type MotionLevel,
  type SensoryProfile,
  type SoundLevel,
} from "./types";

export type EffectiveMotion = MotionLevel;

export interface SensoryContextValue {
  /** What the person chose. */
  profile: SensoryProfile;
  /** Replace the whole profile. */
  setProfile: (profile: SensoryProfile) => void;
  /** Change one field. */
  setOption: <K extends keyof SensoryProfile>(key: K, value: SensoryProfile[K]) => void;
  /** Overwhelmed mode, on for this tab only. */
  overwhelmed: boolean;
  setOverwhelmed: (active: boolean) => void;
  /** Motion actually in force (never more than the OS or `none` allows). */
  motion: EffectiveMotion;
  /** Sound actually in force (`off` while overwhelmed). */
  sound: SoundLevel;
  /** Density actually in force (`simple` while overwhelmed). */
  density: DensityLevel;
  /** True when the operating system asks for reduced motion. */
  osReducedMotion: boolean;
}

const NOOP_PROFILE = { ...DEFAULT_SENSORY_PROFILE };

const SensoryContext = createContext<SensoryContextValue>({
  profile: NOOP_PROFILE,
  setProfile: () => undefined,
  setOption: () => undefined,
  overwhelmed: false,
  setOverwhelmed: () => undefined,
  motion: "normal",
  sound: "low",
  density: "standard",
  osReducedMotion: false,
});

function effectiveMotion(preferred: MotionLevel, osReduced: boolean): MotionLevel {
  if (osReduced) {
    return "none";
  }
  return preferred;
}

export function SensoryProvider({ children }: { children: ReactNode }): ReactElement {
  const [profile, setProfileState] = useState<SensoryProfile>(() => readSensoryProfile());
  const [overwhelmed, setOverwhelmedState] = useState<boolean>(() => readOverwhelmed());
  const osReduced = usePrefersReducedMotion();

  const motion: MotionLevel = overwhelmed ? "none" : effectiveMotion(profile.motion, osReduced);
  const sound: SoundLevel = overwhelmed ? "off" : profile.sound;
  const density: DensityLevel = overwhelmed ? "simple" : profile.density;

  const setProfile = useCallback((next: SensoryProfile) => {
    setProfileState(next);
    writeSensoryProfile(next);
  }, []);

  const setOption = useCallback<SensoryContextValue["setOption"]>((key, value) => {
    setProfileState((current) => {
      const next = { ...current, [key]: value };
      writeSensoryProfile(next);
      return next;
    });
  }, []);

  const setOverwhelmed = useCallback((active: boolean) => {
    setOverwhelmedState(active);
    writeOverwhelmed(active);
  }, []);

  useEffect(() => {
    const root = document.documentElement;
    root.setAttribute("data-sensory-motion", motion);
    root.setAttribute("data-sensory-motion-choice", profile.motion);
    root.setAttribute("data-sensory-sound", sound);
    root.setAttribute("data-sensory-density", density);
    root.setAttribute("data-sensory-colours", profile.colours);
    root.setAttribute("data-sensory-font", profile.fontSize);
    root.style.setProperty("--manovia-font-scale", String(FONT_SCALE[profile.fontSize]));
  }, [motion, profile.motion, sound, density, profile.colours, profile.fontSize]);

  useEffect(() => {
    const root = document.documentElement;
    if (overwhelmed) {
      root.setAttribute("data-overwhelmed", "true");
    } else {
      root.removeAttribute("data-overwhelmed");
    }
  }, [overwhelmed]);

  const value = useMemo<SensoryContextValue>(
    () => ({
      profile,
      setProfile,
      setOption,
      overwhelmed,
      setOverwhelmed,
      motion,
      sound,
      density,
      osReducedMotion: osReduced,
    }),
    [
      profile,
      setProfile,
      setOption,
      overwhelmed,
      setOverwhelmed,
      motion,
      sound,
      density,
      osReduced,
    ],
  );

  return <SensoryContext.Provider value={value}>{children}</SensoryContext.Provider>;
}

/**
 * The profile. Outside a provider this returns the defaults with no-op setters,
 * so a component rendered in isolation (a story, a narrow unit test) still
 * paints instead of throwing.
 */
export function useSensory(): SensoryContextValue {
  return useContext(SensoryContext);
}

/** Motion as the app is actually allowed to animate it. */
export function useEffectiveMotion(): MotionLevel {
  return useSensory().motion;
}
