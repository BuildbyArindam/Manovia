import { compare, mean, reportable, type Comparison } from "./stats";

export type Kind = "factor" | "activity" | "person" | "place" | "emotion" | "theme";
export interface Factor {
  id: string;
  label: string;
  kind: Kind;
}
export interface Day {
  date: string;
  mood: number;
  factors: Factor[];
  journal?: string;
}
export interface Theme {
  id: string;
  name: string;
  dates: string[];
}
export interface Edge {
  factor: Factor;
  stats: Comparison;
}
export interface Graph {
  nodes: Factor[];
  edges: Edge[];
}
export const CAUTION =
  "This does not prove cause. Other circumstances, missing days and changes over time may explain a pattern.";
export const TOO_SMALL =
  "Not enough days to compare. At least 5 matching and 5 non-matching days are needed.";
export function withThemes(days: Day[], themes: Theme[]): Day[] {
  return days.map((day) => ({
    ...day,
    factors: [
      ...day.factors,
      ...themes
        .filter((t) => t.dates.includes(day.date))
        .map((t) => ({ id: t.id, label: t.name, kind: "theme" as const })),
    ],
  }));
}
export function factorsOf(days: Day[]): Factor[] {
  return [...new Map(days.flatMap((d) => d.factors).map((f) => [f.id, f])).values()].sort((a, b) =>
    a.id.localeCompare(b.id),
  );
}
export function whatIf(days: Day[], id: string): Comparison | null {
  days = aggregateDays(days);
  return compare(
    days.filter((d) => d.factors.some((f) => f.id === id)).map((d) => d.mood),
    days.filter((d) => !d.factors.some((f) => f.id === id)).map((d) => d.mood),
    id,
  );
}
export function constructGraph(days: Day[]): Graph {
  const nodes = factorsOf(days);
  const tested = nodes.map((factor) => ({ factor, stats: whatIf(days, factor.id) }));
  const eligible = tested.filter((e): e is Edge => e.stats !== null);
  return { nodes, edges: eligible.filter((e) => reportable(e.stats, eligible.length)) };
}
export function patternSentence(edge: Edge): string {
  const c = edge.stats;
  return `On days when “${edge.factor.label}” was tagged, your mood tended to be ${c.difference > 0 ? "higher" : "lower"} by ${Math.abs(c.difference).toFixed(2)} points. ${c.n[0]} matching days; ${c.n[1]} non-matching days. 95% bootstrap interval for the difference: ${c.interval[0].toFixed(2)} to ${c.interval[1].toFixed(2)}. Exploratory association; comparisons are corrected together, intervals are not simultaneous.`;
}
/** Multiple check-ins count as ONE day; absence of a tag means unrecorded, not proof of absence. */
export function aggregateDays(rows: Day[]): Day[] {
  const groups = new Map<string, Day[]>();
  for (const row of rows) groups.set(row.date, [...(groups.get(row.date) ?? []), row]);
  return [...groups]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([date, ds]) => ({
      date,
      mood: mean(ds.map((d) => d.mood)),
      factors: factorsOf(ds),
      journal: ds.map((d) => d.journal ?? "").join("\n"),
    }));
}
/** Deterministic, bounded spring/repulsion layout. Settles once; never animates. */
export function layout(graph: Graph): Array<{ id: string; x: number; y: number }> {
  const points = [
    { id: "mood", x: 300, y: 200 },
    ...graph.nodes.map((n, i) => ({
      id: n.id,
      x: 300 + 155 * Math.cos((i * 2 * Math.PI) / Math.max(1, graph.nodes.length)),
      y: 200 + 155 * Math.sin((i * 2 * Math.PI) / Math.max(1, graph.nodes.length)),
    })),
  ];
  for (let step = 0; step < 100; step++) {
    for (const p of points.slice(1)) {
      let fx = (300 - p.x) * 0.005,
        fy = (200 - p.y) * 0.005;
      for (const q of points) {
        if (p === q) continue;
        const dx = p.x - q.x,
          dy = p.y - q.y,
          r2 = Math.max(64, dx * dx + dy * dy);
        fx += (dx * 160) / r2;
        fy += (dy * 160) / r2;
      }
      if (graph.edges.some((e) => e.factor.id === p.id)) {
        fx += (300 - p.x) * 0.008;
        fy += (200 - p.y) * 0.008;
      }
      p.x = Math.max(30, Math.min(570, p.x + fx));
      p.y = Math.max(30, Math.min(370, p.y + fy));
    }
  }
  return points;
}
