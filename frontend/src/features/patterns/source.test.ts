import { afterEach, expect, it, vi } from "vitest";
import { fetchMoodEntries } from "@/features/mood/endpoints";
import { listJournalEntries } from "@/features/journal/endpoints";
import { fetchExerciseHistory } from "@/features/exercises/endpoints";
import { writePrivateEnabled } from "@/features/private/ui/preference";
import { loadSourceData } from "./source";
vi.mock("@/features/mood/endpoints", () => ({ fetchMoodEntries: vi.fn() }));
vi.mock("@/features/journal/endpoints", () => ({ listJournalEntries: vi.fn() }));
vi.mock("@/features/exercises/endpoints", () => ({ fetchExerciseHistory: vi.fn() }));
afterEach(() => {
  vi.resetAllMocks();
  writePrivateEnabled(false);
});
it("Private Mode refuses even the first source request", async () => {
  writePrivateEnabled(true);
  await expect(loadSourceData()).rejects.toThrow("Private Mode");
  expect(fetchMoodEntries).not.toHaveBeenCalled();
  expect(listJournalEntries).not.toHaveBeenCalled();
  expect(fetchExerciseHistory).not.toHaveBeenCalled();
});
it("reads existing sources and assembles daily data without inferred people/places", async () => {
  vi.mocked(fetchMoodEntries).mockResolvedValue({
    entries: [
      { id: "m1", local_date: "2026-10-16", valence: 3, factors: ["sleep"], emotions: ["calm"] },
      { id: "m2", local_date: "2026-10-16", valence: 5, factors: [], emotions: [] },
    ],
  } as Awaited<ReturnType<typeof fetchMoodEntries>>);
  vi.mocked(listJournalEntries).mockResolvedValue({
    items: [{ body: "The garden with Alice", created_at: "2026-10-16T12:00:00" }],
    has_more: false,
  } as Awaited<ReturnType<typeof listJournalEntries>>);
  vi.mocked(fetchExerciseHistory).mockResolvedValue({
    items: [{ exercise_id: "breathing", title: "Breathing", created_at: "2026-10-16T12:00:00" }],
    has_more: false,
  } as Awaited<ReturnType<typeof fetchExerciseHistory>>);
  const s = await loadSourceData();
  expect(fetchMoodEntries).toHaveBeenCalledWith({ limit: 500 });
  expect(s.days).toHaveLength(1);
  expect(s.days[0]!.mood).toBe(4);
  expect(s.days[0]!.factors.map((f) => f.id)).toEqual([
    "activity:breathing",
    "emotion:calm",
    "factor:sleep",
  ]);
  expect(s.days[0]!.journal).toContain("Alice");
});
it("refuses a potentially truncated history rather than pretending it is complete", async () => {
  vi.mocked(fetchMoodEntries).mockResolvedValue({ entries: Array(500).fill({}) } as Awaited<
    ReturnType<typeof fetchMoodEntries>
  >);
  await expect(loadSourceData()).rejects.toThrow("truncated");
  expect(listJournalEntries).not.toHaveBeenCalled();
});
it("paginates journal and exercise history", async () => {
  vi.mocked(fetchMoodEntries).mockResolvedValue({ entries: [] } as unknown as Awaited<
    ReturnType<typeof fetchMoodEntries>
  >);
  vi.mocked(listJournalEntries)
    .mockResolvedValueOnce({ items: [], has_more: true } as unknown as Awaited<
      ReturnType<typeof listJournalEntries>
    >)
    .mockResolvedValueOnce({ items: [], has_more: false } as unknown as Awaited<
      ReturnType<typeof listJournalEntries>
    >);
  vi.mocked(fetchExerciseHistory)
    .mockResolvedValueOnce({ items: [], has_more: true } as unknown as Awaited<
      ReturnType<typeof fetchExerciseHistory>
    >)
    .mockResolvedValueOnce({ items: [], has_more: false } as unknown as Awaited<
      ReturnType<typeof fetchExerciseHistory>
    >);
  await loadSourceData();
  expect(listJournalEntries).toHaveBeenLastCalledWith({ limit: 100, offset: 100 });
  expect(fetchExerciseHistory).toHaveBeenLastCalledWith(20);
});
