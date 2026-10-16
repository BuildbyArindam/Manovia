import {
  constructGraph,
  withThemes,
  aggregateDays,
  factorsOf,
  type Day,
  type Factor,
  type Kind,
  type Theme,
} from "./model";
import { dateAt, type Experiment } from "./experiment";
export interface Snapshot {
  version: 1;
  days: Day[];
  themes: Theme[];
  experiment: Experiment | null;
}
export const EMPTY: Snapshot = { version: 1, days: [], themes: [], experiment: null };
const kinds: Kind[] = ["factor", "activity", "person", "place", "emotion", "theme"];
function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new Error("Expected an object.");
  return value as Record<string, unknown>;
}
function text(v: unknown, max = 120): string {
  if (typeof v !== "string" || !v.trim() || v.length > max) throw new Error("Invalid text.");
  return v;
}
function date(v: unknown): string {
  const s = text(v, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(s) || dateAt(s, 0) !== s)
    throw new Error("Invalid calendar date.");
  return s;
}
export function parseSnapshot(raw: string): Snapshot {
  if (raw.length > 2_000_000) throw new Error("File exceeds 2 MB.");
  const v = object(JSON.parse(raw));
  if (v.version !== 1 || !Array.isArray(v.days) || v.days.length > 1000)
    throw new Error("Expected version 1 and at most 1000 check-ins.");
  const rows: Day[] = v.days.map((row) => {
    const d = object(row);
    if (
      typeof d.mood !== "number" ||
      !Number.isFinite(d.mood) ||
      d.mood < 1 ||
      d.mood > 5 ||
      !Array.isArray(d.factors) ||
      d.factors.length > 60
    )
      throw new Error("Invalid mood or tags.");
    const factors: Factor[] = d.factors.map((rawFactor) => {
      const f = object(rawFactor);
      if (!kinds.includes(f.kind as Kind) || f.kind === "theme")
        throw new Error("Invalid tag kind (themes are separate).");
      const id = text(f.id);
      if (id === "mood" || id.startsWith("theme:")) throw new Error("Reserved factor id.");
      return { id, label: text(f.label), kind: f.kind as Kind };
    });
    return {
      date: date(d.date),
      mood: d.mood,
      factors,
      ...(typeof d.journal === "string"
        ? { journal: d.journal.length <= 100_000 ? d.journal : text(d.journal, 100_000) }
        : {}),
    };
  });
  const days = aggregateDays(rows);
  if (days.length > 365 || factorsOf(days).length > 60)
    throw new Error("Limit: 365 days and 60 distinct tags. Choose a smaller window.");
  const themes: Theme[] = Array.isArray(v.themes)
    ? v.themes.map((rawTheme) => {
        const t = object(rawTheme);
        const id = text(t.id);
        if (!id.startsWith("theme:") || !Array.isArray(t.dates)) throw new Error("Invalid theme.");
        return {
          id,
          name: text(t.name),
          dates: [...new Set(t.dates.map(date))].filter((d) => days.some((day) => day.date === d)),
        };
      })
    : [];
  if (themes.length > 20 || new Set(themes.map((t) => t.id)).size !== themes.length)
    throw new Error("Invalid theme count or duplicate id.");
  let experiment: Experiment | null = null;
  if (v.experiment != null) {
    const e = object(v.experiment);
    if (
      !Array.isArray(e.assigned) ||
      e.assigned.length !== 7 ||
      new Set(e.assigned).size !== 7 ||
      e.assigned.some((i) => !Number.isInteger(i) || i < 0 || i > 13)
    )
      throw new Error("Invalid assignment.");
    const start = date(e.start),
      moods: Record<string, number> = {};
    for (const [key, value] of Object.entries(object(e.moods))) {
      if (
        !Array.from({ length: 14 }, (_, i) => dateAt(start, i)).includes(key) ||
        typeof value !== "number" ||
        !Number.isFinite(value) ||
        value < 1 ||
        value > 5
      )
        throw new Error("Invalid experiment mood.");
      moods[key] = value;
    }
    experiment = { activity: text(e.activity), start, assigned: e.assigned as number[], moods };
  }
  return { version: 1, days, themes, experiment };
}
/** Never include journal text in an export. Personal derived data still needs care. */
export function exportSnapshot(s: Snapshot): string {
  return JSON.stringify(
    {
      ...s,
      graph: constructGraph(withThemes(s.days, s.themes)),
      days: s.days.map(({ date, mood, factors }) => ({ date, mood, factors })),
    },
    null,
    2,
  );
}
