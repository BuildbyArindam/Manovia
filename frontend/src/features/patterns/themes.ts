import type { Day, Theme } from "./model";

/** Lightweight lexical topic modelling: TF-IDF vectors + greedy cosine clustering.
 * No clinical vocabulary or inferred labels. Numbers only until the person names them.
 * All scripts supported by Unicode tokenisation; lexical overlap, not semantic inference.
 */
export function discoverThemes(days: Day[]): Theme[] {
  const documents = days.filter((d) => d.journal?.trim());
  const tokens = documents.map((d) => [
    ...new Set(d.journal!.toLocaleLowerCase("und").match(/[\p{L}\p{M}]{3,}/gu) ?? []),
  ]);
  const df = new Map<string, number>();
  tokens.forEach((ts) => ts.forEach((t) => df.set(t, (df.get(t) ?? 0) + 1)));
  const vectors = tokens.map(
    (ts) =>
      new Map(
        ts
          .filter((t) => df.get(t)! < documents.length * 0.8)
          .map((t) => [t, Math.log((documents.length + 1) / (df.get(t)! + 1)) + 1]),
      ),
  );
  const similarity = (a: Map<string, number>, b: Map<string, number>): number => {
    const norm = (v: Map<string, number>): number =>
      Math.sqrt([...v.values()].reduce((s, x) => s + x * x, 0));
    return [...a].reduce((s, [t, x]) => s + x * (b.get(t) ?? 0), 0) / (norm(a) * norm(b) || 1);
  };
  const clusters: number[][] = [];
  vectors.forEach((v, i) => {
    const cluster = clusters.find((c) => similarity(v, vectors[c[0]!]!) >= 0.25);
    if (cluster) cluster.push(i);
    else clusters.push([i]);
  });
  return clusters
    .filter((c) => c.length >= 2)
    .slice(0, 20)
    .map((c, i) => ({
      id: `theme:${i}`,
      name: `Theme ${i + 1}`,
      dates: c.map((j) => documents[j]!.date),
    }));
}
export function mergeThemes(themes: Theme[], source: string, target: string): Theme[] {
  if (source === target) return themes;
  const from = themes.find((t) => t.id === source);
  return themes
    .filter((t) => t.id !== source)
    .map((t) =>
      t.id === target ? { ...t, dates: [...new Set([...t.dates, ...(from?.dates ?? [])])] } : t,
    );
}
