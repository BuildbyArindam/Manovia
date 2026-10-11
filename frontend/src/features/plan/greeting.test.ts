/**
 * The greeting is a time of day and nothing else: no name, no gender, no
 * invented familiarity. These tests pin the hour bands and the copy.
 */
import { describe, expect, it } from "vitest";

import { dayPartForHour, greetingForHour, greetingNow } from "./greeting";

describe("dayPartForHour", () => {
  it.each([
    [4, "evening"],
    [5, "morning"],
    [11, "morning"],
    [12, "afternoon"],
    [16, "afternoon"],
    [17, "evening"],
    [23, "evening"],
    [0, "evening"],
  ])("maps hour %i to %s", (hour, expected) => {
    expect(dayPartForHour(hour)).toBe(expected);
  });
});

describe("greetingForHour", () => {
  it("greets by time of day only", () => {
    expect(greetingForHour(8).headline).toBe("Good morning");
    expect(greetingForHour(14).headline).toBe("Good afternoon");
    expect(greetingForHour(21).headline).toBe("Good evening");
  });

  it("never assumes a name or gender", () => {
    for (let hour = 0; hour < 24; hour += 1) {
      const greeting = greetingForHour(hour);
      expect(greeting.headline).not.toMatch(/(mr|mrs|ms|sir|madam|friend|buddy|guy)/i);
      expect(greeting.qualifier.length).toBeGreaterThan(0);
    }
  });
});

describe("greetingNow", () => {
  it("uses the supplied clock", () => {
    const morning = greetingNow(new Date("2026-10-11T08:00:00"));
    expect(morning.headline).toBe("Good morning");
    const evening = greetingNow(new Date("2026-10-11T20:00:00"));
    expect(evening.headline).toBe("Good evening");
  });
});
