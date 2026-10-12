/**
 * The home greeting: a time-of-day phrase and nothing more.
 *
 * No name, no gender, no "friend" — the app does not know who is holding the
 * phone, and inventing familiarity reads as false. The hour bands match the
 * planner's own `day_part` so the greeting and the suggestions never disagree
 * about what part of the day it is.
 *
 * Day 23: the words come from the translation layer, so a Hindi morning says
 * its greeting in Hindi; the time bands stay here because they are logic, not
 * copy.
 */

import { t } from "@/lib/i18n/i18n";

export interface Greeting {
  headline: string;
  qualifier: string;
}

export type DayPart = "morning" | "afternoon" | "evening";

/** `morning` (05-11), `afternoon` (12-16), `evening` (17-04). */
export function dayPartForHour(hour: number): DayPart {
  if (hour >= 5 && hour < 12) {
    return "morning";
  }
  if (hour >= 12 && hour < 17) {
    return "afternoon";
  }
  return "evening";
}

export function greetingForHour(hour: number): Greeting {
  switch (dayPartForHour(hour)) {
    case "morning":
      return { headline: t("home.greeting.morning"), qualifier: t("home.greeting.qualifier") };
    case "afternoon":
      return { headline: t("home.greeting.afternoon"), qualifier: t("home.greeting.qualifier") };
    case "evening":
      return { headline: t("home.greeting.evening"), qualifier: t("home.greeting.qualifier") };
  }
}

/** The greeting for the device's current time. */
export function greetingNow(now: Date = new Date()): Greeting {
  return greetingForHour(now.getHours());
}
