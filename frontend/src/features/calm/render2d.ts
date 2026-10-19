/**
 * The 2D calm-space painter (Day 46).
 *
 * This is the fallback that has to work everywhere: no WebGL, no WebXR, no
 * audio, no network, no images. It draws a still evening lake with the same
 * low-poly hills as the XR scene and one breathing orb.
 *
 * The painter takes a structurally typed subset of `CanvasRenderingContext2D`
 * rather than the DOM type, which lets the frame-time script exercise the real
 * drawing code against a recording stub in Node — no browser required — and
 * lets the tests assert what was drawn (for example: no `rotate`, ever, and a
 * bounded number of draw calls per frame).
 *
 * The scene is static. Only the orb's radius changes, once per frame, by a
 * fraction small enough that at the 6-breaths-per-minute pace no frame-to-frame
 * change is perceptible as a pulse.
 */

import { breathAt, type BreathPattern } from "./breathing";
import { CALM_PALETTE, HILL_POINTS, HORIZON_FRACTION, TREES, type CalmPalette } from "./scene";

export interface Gradient2D {
  addColorStop(offset: number, color: string): void;
}

export interface Canvas2DLike {
  fillStyle: string | Gradient2D;
  strokeStyle: string | Gradient2D;
  lineWidth: number;
  globalAlpha: number;
  save(): void;
  restore(): void;
  clearRect(x: number, y: number, width: number, height: number): void;
  fillRect(x: number, y: number, width: number, height: number): void;
  beginPath(): void;
  moveTo(x: number, y: number): void;
  lineTo(x: number, y: number): void;
  closePath(): void;
  arc(x: number, y: number, radius: number, start: number, end: number): void;
  fill(): void;
  createLinearGradient(x0: number, y0: number, x1: number, y1: number): Gradient2D;
  createRadialGradient(
    x0: number,
    y0: number,
    r0: number,
    x1: number,
    y1: number,
    r1: number,
  ): Gradient2D;
}

export interface CalmFrame {
  width: number;
  height: number;
  /** Milliseconds since the calm space opened. */
  elapsedMs: number;
  /** Draw the orb at all. False under "motion: none". */
  animateOrb: boolean;
  palette?: CalmPalette;
  pattern?: BreathPattern;
}

/** A very low temporary value: one gradient fill over the whole sky. */
const SKY_STOPS: ReadonlyArray<readonly [number, string]> = [
  [0, CALM_PALETTE.skyTop],
  [1, CALM_PALETTE.skyBottom],
];

function polygon(
  ctx: Canvas2DLike,
  width: number,
  height: number,
  horizon: number,
  points: ReadonlyArray<readonly [number, number]>,
  layerOffset: number,
): void {
  // Each layer is the same silhouette, offset a little vertically, closed
  // below the horizon so the water covers the join. All static.
  const bottom = horizon + height * 0.04;
  ctx.beginPath();
  ctx.moveTo(0, bottom);
  for (const [x, y] of points) {
    ctx.lineTo(x * width, horizon + layerOffset * height + y * height);
  }
  ctx.lineTo(width, bottom);
  ctx.closePath();
  ctx.fill();
}

/**
 * Draw one frame. Allocations are tiny and bounded (two gradients and no
 * arrays); the caller owns the animation clock.
 */
export function drawCalmFrame(ctx: Canvas2DLike, frame: CalmFrame): void {
  const palette = frame.palette ?? CALM_PALETTE;
  const { width, height } = frame;
  const horizon = Math.round(height * HORIZON_FRACTION);
  const breath = breathAt(frame.elapsedMs, frame.pattern);

  ctx.save();
  ctx.globalAlpha = 1;

  // Sky. Two stops, never re-created per frame in the component's real loop —
  // see `CalmCanvas`, which caches the palette on the context.
  const sky = ctx.createLinearGradient(0, 0, 0, horizon);
  for (const [offset, color] of SKY_STOPS) {
    sky.addColorStop(offset, color === CALM_PALETTE.skyTop ? palette.skyTop : palette.skyBottom);
  }
  ctx.fillStyle = sky;
  ctx.fillRect(0, 0, width, horizon);

  // The low-poly hills: three flat silhouettes, no gradients, no movement.
  ctx.fillStyle = palette.hillFar;
  polygon(ctx, width, height, horizon, HILL_POINTS, -0.02);
  ctx.fillStyle = palette.hillMid;
  polygon(ctx, width, height, horizon, HILL_POINTS, 0);
  ctx.fillStyle = palette.hillNear;
  polygon(ctx, width, height, horizon, HILL_POINTS, 0.02);

  // Water, with one static reflection band under the orb.
  ctx.fillStyle = palette.water;
  ctx.fillRect(0, horizon, width, height - horizon);
  ctx.globalAlpha = 0.18;
  ctx.fillStyle = palette.waterReflection;
  ctx.fillRect(Math.round(width * 0.4), horizon + 2, Math.round(width * 0.2), 3);
  ctx.fillRect(Math.round(width * 0.44), horizon + 7, Math.round(width * 0.12), 2);
  ctx.globalAlpha = 1;

  // Four low-poly firs on the near bank. Static.
  for (const [xFraction, treeHeight] of TREES) {
    const x = xFraction * width;
    const base = horizon + height * 0.02;
    const trunk = height * treeHeight * 0.3;
    const crown = height * treeHeight;
    ctx.fillStyle = palette.trunk;
    ctx.fillRect(Math.round(x - 1), Math.round(base - trunk), 2, Math.round(trunk));
    ctx.fillStyle = palette.ground;
    ctx.beginPath();
    ctx.moveTo(x - crown * 0.45, base - trunk);
    ctx.lineTo(x, base - trunk - crown);
    ctx.lineTo(x + crown * 0.45, base - trunk);
    ctx.closePath();
    ctx.fill();
  }

  // The breathing orb. Its *size* follows the breath; its colour and alpha do
  // not, so there is no luminance flash even if a frame is dropped.
  if (frame.animateOrb) {
    const base = Math.min(width, height) * 0.13;
    const radius = base * breath.scale;
    const cx = width * 0.5;
    const cy = horizon * 0.55;
    const glow = ctx.createRadialGradient(cx, cy, radius * 0.1, cx, cy, radius);
    glow.addColorStop(0, palette.orb);
    glow.addColorStop(0.72, palette.orb);
    glow.addColorStop(1, palette.orbEdge);
    ctx.fillStyle = glow;
    ctx.beginPath();
    ctx.arc(cx, cy, radius, 0, Math.PI * 2);
    ctx.closePath();
    ctx.fill();
  }

  ctx.restore();
}

/** Where the orb is drawn, for the DOM mirror and the tests. */
export function orbGeometry(
  width: number,
  height: number,
  scale: number,
): {
  cx: number;
  cy: number;
  radius: number;
} {
  return {
    cx: width * 0.5,
    cy: height * HORIZON_FRACTION * 0.55,
    radius: Math.min(width, height) * 0.13 * scale,
  };
}
