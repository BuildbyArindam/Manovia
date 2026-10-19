import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import {
  EXPECTED_TRIANGLES,
  ORB_SCALE_MAX,
  ORB_SCALE_MIN,
  SEATED_EYE_HEIGHT,
  buildSceneTriangles,
  createCalmWebGL,
  orbScaleForBreath,
} from "./renderWebGL";
import { CALM_PALETTE } from "./scene";
import { createFakeGl } from "./testGl";

const HERE = dirname(fileURLToPath(import.meta.url));

const VIEW = {
  projectionMatrix: new Float32Array(16),
  transform: { inverse: { matrix: new Float32Array(16) } },
};
const VIEWPORT = { x: 0, y: 0, width: 800, height: 600 };

describe("the WebGL calm scene", () => {
  it("is a pinned, low-poly triangle budget", () => {
    expect(EXPECTED_TRIANGLES).toBe(36);
    expect(buildSceneTriangles()).toHaveLength(36 * 3);
  });

  it("puts the ground at a fixed seated eye height — the horizon cannot move", () => {
    const ground = buildSceneTriangles().filter(
      (vertex) => vertex.position[1] === -SEATED_EYE_HEIGHT,
    );
    expect(ground.length).toBeGreaterThan(0);
    expect(SEATED_EYE_HEIGHT).toBe(1.2);
  });

  it("marks only the orb for the breathing scale", () => {
    const orb = buildSceneTriangles().filter((vertex) => vertex.orb === true);
    // Eight triangles, three vertices each.
    expect(orb).toHaveLength(24);
  });

  it("compiles, uploads once and draws a view", () => {
    const { gl, record } = createFakeGl();
    const scene = createCalmWebGL(gl);
    expect(scene).not.toBeNull();
    expect(record.bufferBytes).toBe(36 * 3 * 7 * 4);
    scene?.draw(VIEW, VIEWPORT, 1, null);
    expect(record.draws).toBe(1);
    scene?.draw(VIEW, VIEWPORT, 1.05, null);
    expect(record.draws).toBe(2);
    // No buffer re-upload on the second frame.
    expect(record.bufferBytes).toBe(36 * 3 * 7 * 4);
  });

  it("uses one constant clear colour (no fade, no strobe)", () => {
    const { gl, record } = createFakeGl();
    const scene = createCalmWebGL(gl);
    scene?.draw(VIEW, VIEWPORT, 1, null);
    const first = record.calls.filter((call) => call.name === "clearColor");
    scene?.draw(VIEW, VIEWPORT, 0.9, null);
    const second = record.calls.filter((call) => call.name === "clearColor");
    expect(second).toEqual([...first, ...first]);
  });

  it("returns null instead of throwing when shaders cannot compile", () => {
    const broken = createFakeGl({ failShaders: true });
    expect(createCalmWebGL(broken.gl)).toBeNull();
    const unlinked = createFakeGl({ failLink: true });
    expect(createCalmWebGL(unlinked.gl)).toBeNull();
  });

  it("maps the breath onto a narrow, bounded world scale", () => {
    expect(orbScaleForBreath(0.55)).toBeCloseTo(ORB_SCALE_MIN, 6);
    expect(orbScaleForBreath(1)).toBeCloseTo(ORB_SCALE_MAX, 6);
    expect(orbScaleForBreath(Number.NaN)).toBe(ORB_SCALE_MIN);
    expect(orbScaleForBreath(99)).toBe(ORB_SCALE_MAX);
  });

  it("keeps the palette static and free of pure white", () => {
    for (const colour of Object.values(CALM_PALETTE)) {
      expect(colour).toMatch(/^#[0-9a-f]{6}$/i);
      expect(colour.toLowerCase()).not.toBe("#ffffff");
    }
  });

  it("contains no locomotion, random noise or camera animation", () => {
    const source = readFileSync(join(HERE, "renderWebGL.ts"), "utf8");
    expect(source).not.toMatch(/Math\.random/);
    expect(source).not.toMatch(/requestAnimationFrame/);
    expect(source).not.toMatch(/\btranslate\b|\brotate\b/);
    // The headset's pose is the only camera input.
    expect(source).toContain("view.transform.inverse.matrix");
    expect(source).toContain("view.projectionMatrix");
  });
});
