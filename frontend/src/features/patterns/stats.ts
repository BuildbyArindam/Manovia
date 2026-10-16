/** Browser port of Day 16's resampling methods (ADR 0042).
 * Independent deterministic draw streams; no server, model or telemetry.
 */
export const MIN_N = 5;
export const DRAWS = 2000;
export const mean = (xs: readonly number[]): number => xs.reduce((a, b) => a + b, 0) / xs.length;
export function seeded(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (Math.imul(1664525, state) + 1013904223) >>> 0;
    return state / 4294967296;
  };
}
export function shuffle<T>(xs: readonly T[], rng: () => number): T[] {
  const result = [...xs];
  for (let i = result.length - 1; i > 0; i--) {
    const j = Math.floor(rng() * (i + 1));
    [result[i], result[j]] = [result[j]!, result[i]!];
  }
  return result;
}
function hash(key: string): number {
  let h = 20261016;
  for (const ch of key) h = Math.imul(h ^ ch.charCodeAt(0), 16777619);
  return h >>> 0;
}
export interface Comparison {
  n: [number, number];
  means: [number, number];
  difference: number;
  d: number | null;
  interval: [number, number];
  meanIntervals: [[number, number], [number, number]];
  p: number;
  distributions: [number[], number[]];
}
function interval(xs: number[]): [number, number] {
  xs.sort((a, b) => a - b);
  return [xs[Math.floor(xs.length * 0.025)]!, xs[Math.floor(xs.length * 0.975) - 1]!];
}
export function compare(a: number[], b: number[], key: string): Comparison | null {
  if (a.length < MIN_N || b.length < MIN_N) return null;
  const avgA = mean(a),
    avgB = mean(b),
    difference = avgA - avgB;
  const pool = [...a, ...b],
    rng = seeded(hash(`perm:${key}`));
  let extreme = 0;
  for (let i = 0; i < DRAWS; i++) {
    const draw = shuffle(pool, rng);
    if (
      Math.abs(mean(draw.slice(0, a.length)) - mean(draw.slice(a.length))) >=
      Math.abs(difference) - 1e-12
    )
      extreme++;
  }
  const boot = seeded(hash(`boot:${key}`)),
    diffs: number[] = [],
    aa: number[] = [],
    bb: number[] = [];
  for (let i = 0; i < DRAWS; i++) {
    const x = mean(a.map(() => a[Math.floor(boot() * a.length)]!));
    const y = mean(b.map(() => b[Math.floor(boot() * b.length)]!));
    diffs.push(x - y);
    aa.push(x);
    bb.push(y);
  }
  const ss = (xs: number[], avg: number): number => xs.reduce((s, x) => s + (x - avg) ** 2, 0);
  const spread = Math.sqrt((ss(a, avgA) + ss(b, avgB)) / (a.length + b.length - 2));
  return {
    n: [a.length, b.length],
    means: [avgA, avgB],
    difference,
    d: spread > 0 ? difference / spread : null,
    interval: interval(diffs),
    meanIntervals: [interval(aa), interval(bb)],
    p: (extreme + 1) / (DRAWS + 1),
    distributions: [a, b],
  };
}
export function reportable(c: Comparison, familySize: number): boolean {
  return (
    Math.abs(c.difference) >= 0.4 &&
    c.p <= 0.05 / Math.max(1, familySize) &&
    (c.interval[0] > 0 || c.interval[1] < 0)
  );
}
