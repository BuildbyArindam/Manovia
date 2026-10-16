/** The only network boundary: explicit READS of existing source data in standard mode.
 * Never receives a graph, theme or experiment; never posts derived data.
 */
import { fetchMoodEntries } from "@/features/mood/endpoints";
import { listJournalEntries, type JournalEntry } from "@/features/journal/endpoints";
import type { ExerciseCompletion } from "@/features/exercises/types";
import { fetchExerciseHistory } from "@/features/exercises/endpoints";
import { readPrivateEnabled } from "@/features/private/ui/preference";
import { parseSnapshot, type Snapshot } from "./input";
import type { Factor } from "./model";
export async function loadSourceData(): Promise<Snapshot> {
  if (readPrivateEnabled())
    throw new Error("Private Mode: use a local file. Server reads are disabled.");
  const mood = await fetchMoodEntries({ limit: 500 });
  if (mood.entries.length >= 500)
    throw new Error("History may be truncated. Import a smaller local window instead.");
  const journals: JournalEntry[] = [],
    activities: ExerciseCompletion[] = [];
  for (let offset = 0; ; offset += 100) {
    const page = await listJournalEntries({ limit: 100, offset });
    journals.push(...page.items);
    if (!page.has_more) break;
    if (journals.length >= 1000) throw new Error("Too many journal entries; use a local window.");
  }
  for (let offset = 0; ; offset += 20) {
    const page = await fetchExerciseHistory(offset);
    activities.push(...page.items);
    if (!page.has_more) break;
    if (activities.length >= 1000) throw new Error("Too many exercises; use a local window.");
  }
  // Use the person's mood calendar day; journal/exercise timestamps converted to device-local dates.
  const localDate = (stamp: string): string => {
    const d = new Date(stamp);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  };
  return parseSnapshot(
    JSON.stringify({
      version: 1,
      days: mood.entries.map((m) => ({
        date: m.local_date,
        mood: m.valence,
        factors: [
          ...m.factors.map((label) => ({ id: `factor:${label}`, label, kind: "factor" })),
          ...m.emotions.map((label) => ({ id: `emotion:${label}`, label, kind: "emotion" })),
          ...activities
            .filter((a) => localDate(a.created_at) === m.local_date)
            .map(
              (a) =>
                ({
                  id: `activity:${a.exercise_id}`,
                  label: a.title,
                  kind: "activity",
                }) satisfies Factor,
            ),
        ],
        journal: journals
          .filter((j) => localDate(j.created_at) === m.local_date)
          .map((j) => j.body)
          .join("\n"),
      })),
    }),
  );
}
