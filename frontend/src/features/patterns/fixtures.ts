/** Hand-written synthetic generator only: no real user data. */
import type { Day, Factor } from "./model";
import { seeded } from "./stats";
export function synthetic(seed: number, signal = true): Day[] {
  const rng = seeded(seed * 7919 + 4181);
  return Array.from({ length: 120 }, (_, i) => {
    const walk = rng() < 0.5,
      sleep = rng() < 0.5;
    const factors: Factor[] = [];
    if (walk) factors.push({ id: "walk", label: "A walk", kind: "activity" });
    if (sleep) factors.push({ id: "sleep", label: "7+ hours of sleep", kind: "factor" });
    for (let j = 0; j < 8; j++)
      if (rng() < 0.5) factors.push({ id: `noise${j}`, label: `Noise ${j}`, kind: "factor" });
    const d = new Date(Date.UTC(2026, 0, i + 1));
    return {
      date: d.toISOString().slice(0, 10),
      mood: signal ? 2 + Number(walk) + 0.7 * Number(sleep) + (rng() - 0.5) * 0.6 : 1 + 4 * rng(),
      factors,
      journal: i % 2 === 0 ? "walking trees garden sunshine" : "reading library books stories",
    };
  });
}
