/**
 * The vocabulary guards.
 *
 * `scripts/verify_day15.py` compares these ids against the Python source of
 * truth; this file checks the properties that matter on their own — that a chip
 * always has a word, that the face scale is never a colour and a number, and that
 * the five faces are five *different* shapes rather than five fills of a circle.
 */
import {
  EMOTION_IDS,
  ENERGY_POINTS,
  FACTOR_IDS,
  MAX_EMOTIONS,
  MAX_FACTORS,
  MOOD_EMOTIONS,
  MOOD_FACTORS,
  NOTE_MAX_CHARS,
  SCALE_MAX,
  SCALE_MIN,
  VALENCE_POINTS,
  emotionLabel,
  energyPoint,
  factorLabel,
  valencePoint,
} from "./taxonomy";

describe("mood vocabulary", () => {
  it("offers the nine emotions the analyser knows", () => {
    expect(EMOTION_IDS).toEqual([
      "joy",
      "sadness",
      "anger",
      "fear",
      "anxiety",
      "shame",
      "loneliness",
      "calm",
      "neutral",
    ]);
  });

  it("offers the eight context factors the server accepts", () => {
    expect(FACTOR_IDS).toEqual([
      "sleep",
      "study",
      "work",
      "family",
      "friends",
      "health",
      "money",
      "weather",
    ]);
  });

  it("keeps the caps and the scale in step with the backend", () => {
    expect(MAX_EMOTIONS).toBe(3);
    expect(MAX_FACTORS).toBe(5);
    expect(NOTE_MAX_CHARS).toBe(1000);
    expect(SCALE_MIN).toBe(1);
    expect(SCALE_MAX).toBe(5);
  });

  it("gives every chip a word a person would use", () => {
    for (const option of [...MOOD_EMOTIONS, ...MOOD_FACTORS]) {
      expect(option.label.length).toBeGreaterThan(1);
      expect(option.label).not.toBe(option.id);
      expect(option.label).not.toMatch(/[_-]/);
      // Sentence case, not SHOUTING and not clinical.
      expect(option.label.charAt(0)).toBe(option.label.charAt(0).toUpperCase());
    }
  });

  it("has no duplicate ids or labels", () => {
    const ids = [...EMOTION_IDS, ...FACTOR_IDS];
    const labels = [...MOOD_EMOTIONS, ...MOOD_FACTORS].map((option) => option.label);
    expect(new Set(ids).size).toBe(ids.length);
    expect(new Set(labels).size).toBe(labels.length);
  });

  it("labels every point on both scales", () => {
    expect(VALENCE_POINTS).toHaveLength(5);
    expect(ENERGY_POINTS).toHaveLength(5);
    for (const point of VALENCE_POINTS) {
      expect(point.label.length).toBeGreaterThan(1);
    }
    for (const point of ENERGY_POINTS) {
      expect(point.label.length).toBeGreaterThan(1);
    }
  });

  it("draws five different faces, not one face in five colours", () => {
    const shapes = VALENCE_POINTS.map((point) => point.face);
    expect(new Set(shapes).size).toBe(shapes.length);
    for (const shape of shapes) {
      expect(shape).toMatch(/^M[\d.\s]/);
    }
  });

  it("orders both scales from one to five", () => {
    expect(VALENCE_POINTS.map((point) => point.value)).toEqual([1, 2, 3, 4, 5]);
    expect(ENERGY_POINTS.map((point) => point.value)).toEqual([1, 2, 3, 4, 5]);
    expect(VALENCE_POINTS[0]?.label).toBe("Very low");
    expect(VALENCE_POINTS[4]?.label).toBe("Very good");
  });

  it("falls back to the middle rather than throwing on an unknown value", () => {
    expect(valencePoint(0).value).toBe(3);
    expect(valencePoint(99).value).toBe(3);
    expect(energyPoint(-1).value).toBe(3);
  });

  it("shows an unknown stored id as itself instead of losing it", () => {
    // A future server-side tag must not turn a past check-in into "undefined".
    expect(emotionLabel("wistfulness")).toBe("wistfulness");
    expect(factorLabel("commute")).toBe("commute");
    expect(emotionLabel("joy")).toBe("Joy");
  });
});
