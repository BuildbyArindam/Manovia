/**
 * The reminder preference store: defaults, validation, and never throwing.
 *
 * Storage is hostile territory — private mode, a full quota, a value written by
 * an older build — and a page that crashes because a *preference* could not be
 * read would be an absurd way to lose somebody's check-in. Every read path here
 * falls back to the default; every write path swallows the failure.
 */
import {
  DEFAULT_MOOD_REMINDER,
  MOOD_REMINDER_STORAGE_KEY,
  isValidReminderTime,
  loadMoodReminder,
  saveMoodReminder,
} from "./storage";

function memoryStorage(): Storage {
  const map = new Map<string, string>();
  return {
    get length(): number {
      return map.size;
    },
    clear: () => map.clear(),
    getItem: (key: string) => map.get(key) ?? null,
    key: (index: number) => [...map.keys()][index] ?? null,
    removeItem: (key: string) => {
      map.delete(key);
    },
    setItem: (key: string, value: string) => {
      map.set(key, value);
    },
  };
}

describe("reminder preference", () => {
  it("is off until somebody turns it on", () => {
    expect(loadMoodReminder(memoryStorage())).toEqual(DEFAULT_MOOD_REMINDER);
    expect(DEFAULT_MOOD_REMINDER.enabled).toBe(false);
  });

  it("round-trips a saved preference", () => {
    const storage = memoryStorage();
    saveMoodReminder({ enabled: true, time: "21:30" }, storage);

    expect(loadMoodReminder(storage)).toEqual({ enabled: true, time: "21:30" });
    expect(storage.getItem(MOOD_REMINDER_STORAGE_KEY)).toBe('{"enabled":true,"time":"21:30"}');
  });

  it("refuses a malformed time and keeps the default instead", () => {
    const storage = memoryStorage();
    saveMoodReminder({ enabled: true, time: "25:99" }, storage);

    expect(loadMoodReminder(storage).time).toBe(DEFAULT_MOOD_REMINDER.time);
  });

  it.each(["00:00", "07:05", "23:59"])("accepts %s", (time) => {
    expect(isValidReminderTime(time)).toBe(true);
  });

  it.each(["24:00", "7:05", "20:60", "2000", "", "evening"])("rejects %s", (time) => {
    expect(isValidReminderTime(time)).toBe(false);
  });

  it("falls back to the default on corrupt json", () => {
    const storage = memoryStorage();
    storage.setItem(MOOD_REMINDER_STORAGE_KEY, "{not json");

    expect(loadMoodReminder(storage)).toEqual(DEFAULT_MOOD_REMINDER);
  });

  it("falls back to the default on a record from an older or wrong shape", () => {
    const storage = memoryStorage();
    storage.setItem(MOOD_REMINDER_STORAGE_KEY, JSON.stringify({ enabled: "yes", at: "20:00" }));

    expect(loadMoodReminder(storage)).toEqual(DEFAULT_MOOD_REMINDER);
  });

  it("does not throw when storage itself throws", () => {
    const hostile = {
      getItem: () => {
        throw new Error("blocked");
      },
      setItem: () => {
        throw new Error("quota");
      },
    } as unknown as Storage;

    expect(loadMoodReminder(hostile)).toEqual(DEFAULT_MOOD_REMINDER);
    expect(() => {
      saveMoodReminder({ enabled: true, time: "20:00" }, hostile);
    }).not.toThrow();
  });
});
