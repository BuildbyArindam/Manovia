/**
 * The 2D fallback, asserted through a recording context.
 *
 * This is the same drawing code the browser runs; the stub only records what it
 * was asked to draw. That makes the comfort rules testable without a GPU: a
 * stable horizon means the hill polygons are identical frame to frame, no
 * flashing means no clearRect and no colour change between breaths, and no
 * artificial locomotion means there is no translate/rotate method to call.
 */

import { describe, expect, it } from "vitest";

import { drawCalmFrame, orbGeometry, type Canvas2DLike } from "./render2d";
import { CALM_PALETTE, HORIZON_FRACTION } from "./scene";

interface Op {
  name: string;
  args: number[];
  fill?: string;
}

function recordingContext(): { ctx: Canvas2DLike; ops: Op[]; gradients: string[][] } {
  const ops: Op[] = [];
  const gradients: string[][] = [];
  let fill = "";
  const ctx: Canvas2DLike = {
    get fillStyle() {
      return fill;
    },
    set fillStyle(value: string | { addColorStop: (offset: number, color: string) => void }) {
      fill = typeof value === "string" ? value : "gradient";
      if (typeof value !== "string") {
        const stops: string[] = [];
        const original = value.addColorStop.bind(value);
        value.addColorStop = (offset, color) => {
          stops.push(`${offset}:${color}`);
          original(offset, color);
        };
        gradients.push(stops);
      }
    },
    strokeStyle: "",
    lineWidth: 1,
    globalAlpha: 1,
    save: () => ops.push({ name: "save", args: [] }),
    restore: () => ops.push({ name: "restore", args: [] }),
    clearRect: (...args) => ops.push({ name: "clearRect", args }),
    fillRect: (...args) => ops.push({ name: "fillRect", args, fill }),
    beginPath: () => ops.push({ name: "beginPath", args: [] }),
    moveTo: (...args) => ops.push({ name: "moveTo", args }),
    lineTo: (...args) => ops.push({ name: "lineTo", args }),
    closePath: () => ops.push({ name: "closePath", args: [] }),
    arc: (...args) => ops.push({ name: "arc", args, fill }),
    fill: () => ops.push({ name: "fill", args: [], fill }),
    createLinearGradient: () => ({
      addColorStop: () => undefined,
    }),
    createRadialGradient: () => ({
      addColorStop: () => undefined,
    }),
  } as Canvas2DLike;
  return { ctx, ops, gradients };
}

function draw(elapsedMs: number, animateOrb = true) {
  const { ctx, ops } = recordingContext();
  drawCalmFrame(ctx, { width: 800, height: 500, elapsedMs, animateOrb });
  return ops;
}

describe("the 2D calm scene", () => {
  it("draws the sky, three hill layers, water, four trees and one orb", () => {
    const ops = draw(0);
    expect(ops.filter((op) => op.name === "fill")).toHaveLength(3 + 4 + 1);
    expect(ops.filter((op) => op.name === "fillRect").length).toBeGreaterThanOrEqual(5);
    expect(ops.filter((op) => op.name === "arc")).toHaveLength(1);
    // Bounded work: under a hundred calls a frame, not a particle system.
    expect(ops.length).toBeLessThan(100);
  });

  it("never clears to a flash and never changes colour with the breath", () => {
    const first = draw(0);
    const late = draw(5000);
    expect(first.some((op) => op.name === "clearRect")).toBe(false);
    const coloursFirst = first.filter((op) => op.fill !== undefined).map((op) => op.fill);
    const coloursLate = late.filter((op) => op.fill !== undefined).map((op) => op.fill);
    expect(coloursLate).toEqual(coloursFirst);
  });

  it("keeps the horizon and the landscape byte-identical across frames", () => {
    const a = draw(0).filter((op) => op.name !== "arc" && op.fill !== "gradient");
    const b = draw(7000).filter((op) => op.name !== "arc" && op.fill !== "gradient");
    expect(b).toEqual(a);
    // The horizon is a constant fraction of the frame.
    const water = draw(0).find(
      (op) => op.name === "fillRect" && op.args[1] === Math.round(500 * HORIZON_FRACTION),
    );
    expect(water).toBeDefined();
  });

  it("breathes: the orb grows on the inhale and settles on the exhale", () => {
    const radiusAt = (ms: number): number => {
      const arc = draw(ms).find((op) => op.name === "arc");
      return arc?.args[2] ?? -1;
    };
    expect(radiusAt(4000)).toBeGreaterThan(radiusAt(0));
    expect(radiusAt(5000)).toBeGreaterThan(radiusAt(9000));
    expect(radiusAt(0)).toBeCloseTo(radiusAt(10_000), 5);
  });

  it("draws no orb at all under motion: none", () => {
    const ops = draw(2000, false);
    expect(ops.some((op) => op.name === "arc")).toBe(false);
    expect(orbGeometry(800, 500, 0.8).cx).toBe(400);
  });

  it("uses only the palette's colours", () => {
    const allowed = new Set(Object.values(CALM_PALETTE));
    for (const op of draw(3000)) {
      if (op.fill !== undefined && op.fill !== "gradient") {
        expect(allowed.has(op.fill), `unexpected fill ${op.fill}`).toBe(true);
      }
    }
  });
});
