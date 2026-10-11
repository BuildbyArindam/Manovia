/**
 * The check-in vocabulary, mirrored from `app/services/mood/taxonomy.py`.
 *
 * A test (`taxonomy.test.ts`) pins the counts and the ids, and
 * `scripts/verify_day15.py` compares this file against the Python source, so the
 * two cannot drift apart silently and start 422-ing somebody's check-in.
 *
 * Two rules shape every entry here:
 *
 * 1. **A label is a word a person would use about themselves**, not a clinical
 *    term. There is no "dysphoric" and no "elevated".
 * 2. **Nothing is colour-only.** Every scale point carries a text label *and* a
 *    differently-shaped face; every chip is a word before it is a colour.
 */

/** The nine emotion labels — the same taxonomy the analyser uses. */
export interface EmotionOption {
  id: string;
  label: string;
}

export const MOOD_EMOTIONS: readonly EmotionOption[] = [
  { id: "joy", label: "Joy" },
  { id: "sadness", label: "Sadness" },
  { id: "anger", label: "Anger" },
  { id: "fear", label: "Fear" },
  { id: "anxiety", label: "Anxiety" },
  { id: "shame", label: "Shame" },
  { id: "loneliness", label: "Loneliness" },
  { id: "calm", label: "Calm" },
  { id: "neutral", label: "Flat" },
] as const;

/** What might have been going on around the feeling. */
export const MOOD_FACTORS: readonly EmotionOption[] = [
  { id: "sleep", label: "Sleep" },
  { id: "study", label: "Study" },
  { id: "work", label: "Work" },
  { id: "family", label: "Family" },
  { id: "friends", label: "Friends" },
  { id: "health", label: "Health" },
  { id: "money", label: "Money" },
  { id: "weather", label: "Weather" },
] as const;

export const MAX_EMOTIONS = 3;
export const MAX_FACTORS = 5;
export const NOTE_MAX_CHARS = 1000;
export const SCALE_MIN = 1;
export const SCALE_MAX = 5;

/**
 * One point on the valence scale: a word, and a face whose *shape* carries the
 * same meaning as its colour.
 *
 * `face` is the SVG path for the mouth, drawn on a circle with two eyes. The
 * curves are deliberately distinct at a glance — a tester with the colour
 * stripped out (or a colour-blind user under a red filter) must still be able to
 * tell "very low" from "very good" without reading the label.
 */
export interface MoodScalePoint {
  value: number;
  label: string;
  face: string;
}

export const VALENCE_POINTS: readonly MoodScalePoint[] = [
  { value: 1, label: "Very low", face: "M7 18 Q12 11 17 18" },
  { value: 2, label: "Low", face: "M8 17 Q12 13.5 16 17" },
  { value: 3, label: "In the middle", face: "M8 16 L16 16" },
  { value: 4, label: "Good", face: "M8 15 Q12 18.5 16 15" },
  { value: 5, label: "Very good", face: "M7 14 Q12 21 17 14" },
] as const;

/** Energy is a slider, so its points only need words. */
export interface EnergyPoint {
  value: number;
  label: string;
}

export const ENERGY_POINTS: readonly EnergyPoint[] = [
  { value: 1, label: "Empty" },
  { value: 2, label: "Running low" },
  { value: 3, label: "Some" },
  { value: 4, label: "Good" },
  { value: 5, label: "Plenty" },
] as const;

/**
 * The middle point, used as the fallback when a value is outside 1–5. It is
 * written out rather than indexed out of the array so that it is a real value
 * under `noUncheckedIndexedAccess`, and `taxonomy.test.ts` pins that it matches
 * the middle entry of each list.
 */
export const NEUTRAL_VALENCE_POINT: MoodScalePoint = {
  value: 3,
  label: "In the middle",
  face: "M8 16 L16 16",
};
export const NEUTRAL_ENERGY_POINT: EnergyPoint = { value: 3, label: "Some" };

export function valencePoint(value: number): MoodScalePoint {
  return (
    VALENCE_POINTS.find((point) => point.value === value) ??
    VALENCE_POINTS.find((point) => point.value === NEUTRAL_VALENCE_POINT.value) ??
    NEUTRAL_VALENCE_POINT
  );
}

export function energyPoint(value: number): EnergyPoint {
  return (
    ENERGY_POINTS.find((point) => point.value === value) ??
    ENERGY_POINTS.find((point) => point.value === NEUTRAL_ENERGY_POINT.value) ??
    NEUTRAL_ENERGY_POINT
  );
}

export function emotionLabel(id: string): string {
  return MOOD_EMOTIONS.find((option) => option.id === id)?.label ?? id;
}

export function factorLabel(id: string): string {
  return MOOD_FACTORS.find((option) => option.id === id)?.label ?? id;
}

/** The emotion ids the server accepts. Exported for the drift check. */
export const EMOTION_IDS: readonly string[] = MOOD_EMOTIONS.map((option) => option.id);
/** The factor ids the server accepts. */
export const FACTOR_IDS: readonly string[] = MOOD_FACTORS.map((option) => option.id);
