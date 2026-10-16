import { compare, shuffle, type Comparison } from "./stats";
export interface Experiment {
  activity: string;
  start: string;
  assigned: number[];
  moods: Record<string, number>;
}
export function dateAt(start: string, offset: number): string {
  const date = new Date(`${start}T12:00:00Z`);
  date.setUTCDate(date.getUTCDate() + offset);
  return date.toISOString().slice(0, 10);
}
export function createExperiment(
  activity: string,
  start: string,
  rng: () => number = () => crypto.getRandomValues(new Uint32Array(1))[0]! / 4294967296,
): Experiment {
  if (!activity.trim() || !/^\d{4}-\d{2}-\d{2}$/.test(start) || dateAt(start, 0) !== start)
    throw new Error("Choose an activity and a valid start date.");
  return {
    activity: activity.trim().slice(0, 120),
    start,
    assigned: shuffle(
      Array.from({ length: 14 }, (_, i) => i),
      rng,
    )
      .slice(0, 7)
      .sort((a, b) => a - b),
    moods: {},
  };
}
export function analyseExperiment(e: Experiment): Comparison | null {
  // Analyse assigned days, not whether the activity happened.
  const a: number[] = [],
    b: number[] = [];
  for (let i = 0; i < 14; i++) {
    const mood = e.moods[dateAt(e.start, i)];
    if (mood === undefined || !Number.isFinite(mood) || mood < 1 || mood > 5) return null;
    (e.assigned.includes(i) ? a : b).push(mood);
  }
  return compare(a, b, `experiment:${e.start}`);
}
export function experimentSentence(c: Comparison | null): string {
  if (!c) return "Record mood for all 14 days before analysing. Missing days are not filled in.";
  return `On assigned activity days, mood averaged ${c.means[0].toFixed(2)}; on other days, ${c.means[1].toFixed(2)}. ${c.n[0]} and ${c.n[1]} days. Difference ${c.difference.toFixed(2)}; 95% bootstrap interval ${c.interval[0].toFixed(2)} to ${c.interval[1].toFixed(2)}. Two-sided permutation p=${c.p.toFixed(4)}. ${c.p <= 0.05 ? "A difference was observed in this fortnight." : "No clear difference was observed in this fortnight."} One small, unblinded experiment is uncertain; this is not a promise about future mood.`;
}
