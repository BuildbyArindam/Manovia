/**
 * Where the daily reminder preference lives — on the device, and nowhere else.
 *
 * There is no push service yet, and the honest version of that is a setting that
 * is *remembered* and says so, not a toggle that pretends to schedule something.
 * Nothing here is sent to the backend: a reminder time is a small preference, and
 * a mental-health app has no reason to know when somebody wants to be nudged.
 *
 * The record is versioned and validated on read, like the onboarding record, so a
 * half-written or older value fails safely back to the default rather than
 * crashing the page.
 */

export const MOOD_REMINDER_STORAGE_KEY = "manovia.mood.reminder.v1";

export interface MoodReminder {
  enabled: boolean;
  /** 24-hour ``HH:MM``, local to the device. */
  time: string;
}

/** Off by default: a check-in nobody asked to be reminded about is a nag. */
export const DEFAULT_MOOD_REMINDER: MoodReminder = { enabled: false, time: "20:00" };

/** `20:00`, `07:05` — and nothing else. */
export function isValidReminderTime(value: string): boolean {
  return /^([01][0-9]|2[0-3]):[0-5][0-9]$/.test(value);
}

function isMoodReminder(value: unknown): value is MoodReminder {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const record = value as Record<string, unknown>;
  return typeof record.enabled === "boolean" && typeof record.time === "string" && isValidReminderTime(record.time);
}

export function loadMoodReminder(storage: Storage = window.localStorage): MoodReminder {
  try {
    const raw = storage.getItem(MOOD_REMINDER_STORAGE_KEY);
    if (raw === null) {
      return DEFAULT_MOOD_REMINDER;
    }
    const parsed: unknown = JSON.parse(raw);
    return isMoodReminder(parsed) ? parsed : DEFAULT_MOOD_REMINDER;
  } catch {
    return DEFAULT_MOOD_REMINDER;
  }
}

export function saveMoodReminder(reminder: MoodReminder, storage: Storage = window.localStorage): void {
  const safe: MoodReminder = {
    enabled: reminder.enabled,
    time: isValidReminderTime(reminder.time) ? reminder.time : DEFAULT_MOOD_REMINDER.time,
  };
  try {
    storage.setItem(MOOD_REMINDER_STORAGE_KEY, JSON.stringify(safe));
  } catch {
    // Private mode or a blocked origin: the preference simply will not persist.
  }
}
