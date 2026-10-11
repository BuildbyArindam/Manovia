import { describe, expect, it } from "vitest";

import { MOOD_EMOTIONS } from "@/features/mood/taxonomy";
import { CUE_LABELS, EMOTION_OPTIONS, EXERCISE_COPY, formatDuration } from "./copy";

describe("guided exercise copy", () => {
  it("makes no outcome guarantees or treatment claims", () => {
    const copy = Object.values(EXERCISE_COPY).join("\n");
    expect(copy).not.toMatch(
      /will (?:cure|fix|heal|make you feel)|guaranteed|clinically proven|works every time|you failed|lost your streak/i,
    );
    expect(copy).toContain("not treatment");
    expect(copy).toContain("association, not proof");
    expect(copy).toContain("Not saved");
    expect(copy).toContain("Reloading offline is not supported");
  });

  it("uses the existing emotion taxonomy and gives every animation a text cue", () => {
    expect(EMOTION_OPTIONS.map(([id]) => id).sort()).toEqual(
      MOOD_EMOTIONS.map((item) => item.id).sort(),
    );
    expect(Object.keys(CUE_LABELS).sort()).toEqual(
      ["instruction", "inhale", "hold-in", "exhale", "hold-out"].sort(),
    );
    expect(Object.values(CUE_LABELS).every((text) => text.length > 0)).toBe(true);
  });

  it.each([
    [90, "1 min 30 sec"],
    [120, "2 min"],
    [180, "3 min"],
    [15, "15 sec"],
  ])("formats a duration of %s seconds", (seconds, expected) => {
    expect(formatDuration(Number(seconds))).toBe(expected);
  });
});
