/**
 * The calm space scene description (Day 46).
 *
 * Kept as data so both the 2D canvas painter and the WebGL/XR renderer draw the
 * same place, and so the comfort rules can be asserted without a GPU:
 *
 * * **No artificial locomotion.** Nothing in this file is a position the
 *   viewer can be moved to. The 2D painter never scrolls or pans; the XR
 *   renderer uses the headset's own pose and nothing else.
 * * **A stable horizon.** The sky/ground boundary is a constant fraction of
 *   the frame and the hills are static in world coordinates. There is no
 *   camera bob, roll or shake.
 * * **No flashing.** Colours are static, there is no strobing, and the orb's
 *   luminance is constant — only its *size* follows the breath. It can never
 *   change by more than 1/60 of its range in one frame at the design pace,
 *   which the frame-time test checks.
 * * **Low-poly and small.** The whole scene is a few dozen triangles and a
 *   handful of gradients. There are no images, models or fonts to download:
 *   nothing here loads a byte over the network at runtime.
 */

export interface CalmPalette {
  skyTop: string;
  skyBottom: string;
  horizon: string;
  hillNear: string;
  hillMid: string;
  hillFar: string;
  water: string;
  waterReflection: string;
  orb: string;
  orbEdge: string;
  ground: string;
  trunk: string;
}

/**
 * A late-evening lake. Warm, low-saturation, no pure white and no saturated
 * blue: both are what "calm" is not.
 */
export const CALM_PALETTE: CalmPalette = {
  skyTop: "#26384a",
  skyBottom: "#b98f6d",
  horizon: "#e3c3a0",
  hillFar: "#4f5f5a",
  hillMid: "#3d4c46",
  hillNear: "#2c3833",
  water: "#2f4348",
  waterReflection: "#c9a488",
  orb: "#f0d3ab",
  orbEdge: "#c79c72",
  ground: "#3b4b3f",
  trunk: "#33281f",
};

/**
 * The horizon sits at this fraction of the frame height. A constant, because a
 * horizon that drifts is exactly the "stable horizon" rule this screen keeps.
 */
export const HORIZON_FRACTION = 0.58;

/** Low-poly silhouette of the far hills: x fraction + y offset from the horizon. */
export const HILL_POINTS: ReadonlyArray<readonly [number, number]> = [
  [0, 0],
  [0.08, -0.06],
  [0.16, -0.03],
  [0.27, -0.09],
  [0.38, -0.04],
  [0.5, -0.1],
  [0.62, -0.05],
  [0.73, -0.08],
  [0.85, -0.03],
  [0.93, -0.07],
  [1, -0.01],
];

/** Tree positions (x fraction, trunk height fraction of the frame). */
export const TREES: ReadonlyArray<readonly [number, number]> = [
  [0.14, 0.075],
  [0.22, 0.055],
  [0.79, 0.07],
  [0.88, 0.05],
];

/**
 * WebGL triangle budget. The scene is deliberately countable: ground (2),
 * far hills (9), near bank (2), four low-poly trees (4 × 8), the orb (8).
 * 36 triangles. A frame-time regression here would be a real bug, not a
 * driver difference, which is why the number is pinned by a test.
 */
export const WEBGL_TRIANGLE_BUDGET = 36;

/**
 * A static scene means a static seed for any small variation (hill jitter,
 * tree shapes). `Math.random` in a render loop is how "gentle" becomes
 * "twitchy"; the guard test forbids it in renderer modules.
 */
export const SCENE_SEED = 46;
