/**
 * The WebGL scene used inside a WebXR session (Day 46).
 *
 * Hand-written and small on purpose. Three.js or A-Frame would be the obvious
 * choice, but both are hundreds of kilobytes for a scene of a few dozen
 * triangles, and the brief also says no external CDN assets and a stated asset
 * budget. A 200-line renderer with no dependency keeps the calm space's lazy
 * chunk under its budget and makes every draw call reviewable — which matters
 * more than ergonomics for a screen whose job is to not surprise anyone.
 *
 * Comfort rules this renderer is responsible for:
 *
 * * The camera comes from the headset (`XRView.projectionMatrix` and
 *   `XRView.transform.inverse.matrix`) and from nothing else. There is no
 *   position variable to animate, so locomotion cannot creep in later.
 * * The horizon is the ground plane at a fixed height; nothing rolls or pitches
 *   the world.
 * * The only per-frame animation is the orb's scale, driven by the 6 bpm
 *   breathing curve; colour and alpha are constants, so there is no flash.
 * * Buffers and the program are created once. `draw` allocates nothing.
 *
 * It is also used as the "3D canvas" rendering path when WebGL exists and the
 * device has no XR, through the same single-view path.
 */

import { MIN_SCALE } from "./breathing";
import { CALM_PALETTE, TREES, WEBGL_TRIANGLE_BUDGET } from "./scene";

/** Eye height in `local` reference space: seated, and fixed for the session. */
export const SEATED_EYE_HEIGHT = 1.2;

/** Orb scale range in world units. Narrow: a breath, not a throb. */
export const ORB_SCALE_MIN = 0.88;
export const ORB_SCALE_MAX = 1.12;
export const ORB_CENTER: readonly [number, number, number] = [0, -0.12, -1.45];

const VERTEX_SHADER = `
attribute vec3 aPosition;
attribute vec3 aColor;
attribute float aOrb;
uniform mat4 uProjection;
uniform mat4 uView;
uniform vec3 uOrbCenter;
uniform float uOrbScale;
varying vec3 vColor;
void main() {
  vec3 p = aPosition;
  if (aOrb > 0.5) {
    p = uOrbCenter + (aPosition - uOrbCenter) * uOrbScale;
  }
  vColor = aColor;
  gl_Position = uProjection * uView * vec4(p, 1.0);
}
`;

const FRAGMENT_SHADER = `
precision mediump float;
varying vec3 vColor;
void main() {
  gl_FragColor = vec4(vColor, 1.0);
}
`;

type Vec3 = readonly [number, number, number];

interface Vertex {
  position: Vec3;
  color: string;
  orb?: boolean;
}

function hexToRgb(hex: string): Vec3 {
  const value = Number.parseInt(hex.slice(1), 16);
  return [((value >> 16) & 255) / 255, ((value >> 8) & 255) / 255, (value & 255) / 255];
}

function pushTriangle(
  target: Vertex[],
  a: Vec3,
  b: Vec3,
  c: Vec3,
  color: string,
  orb = false,
): void {
  target.push(
    { position: a, color, orb },
    { position: b, color, orb },
    { position: c, color, orb },
  );
}

/** Build the static scene once. Returns the pinned number of triangles. */
export function buildSceneTriangles(): Vertex[] {
  const vertices: Vertex[] = [];
  const ground = -SEATED_EYE_HEIGHT;
  const far = CALM_PALETTE.hillFar;
  const mid = CALM_PALETTE.hillMid;
  const near = CALM_PALETTE.hillNear;

  // Ground: two triangles, a fixed plane. The horizon is its far edge.
  pushTriangle(
    vertices,
    [-30, ground, -40],
    [30, ground, -40],
    [30, ground, 2],
    CALM_PALETTE.ground,
  );
  pushTriangle(
    vertices,
    [-30, ground, -40],
    [30, ground, 2],
    [-30, ground, 2],
    CALM_PALETTE.ground,
  );

  // Far ridge: four triangles, still.
  const ridge: ReadonlyArray<readonly [number, number]> = [
    [-18, 1.4],
    [-9, 2.6],
    [0, 1.9],
    [9, 2.8],
    [18, 1.6],
  ];
  for (let i = 0; i < ridge.length - 1; i += 1) {
    const [x0, y0] = ridge[i] as readonly [number, number];
    const [x1, y1] = ridge[i + 1] as readonly [number, number];
    pushTriangle(vertices, [x0, ground, -22], [x1, ground, -22], [x1, ground + y1, -22], far);
    pushTriangle(vertices, [x0, ground, -22], [x1, ground + y1, -22], [x0, ground + y0, -22], far);
  }

  // Mid ridge, closer and lower.
  pushTriangle(vertices, [-14, ground, -14], [0, ground + 1.5, -14], [14, ground, -14], mid);
  pushTriangle(vertices, [-14, ground, -14], [14, ground, -14], [10, ground, -12], mid);

  // Four low-poly firs: four side triangles each, no cones' bases needed.
  for (const [xFrac, heightFrac] of TREES) {
    const x = (xFrac - 0.5) * 22;
    const z = -6 - (1 - heightFrac) * 8;
    const height = 2.2 + heightFrac * 6;
    const radius = 0.55 + heightFrac * 1.2;
    const apex: Vec3 = [x, ground + height, z];
    pushTriangle(vertices, [x - radius, ground, z], [x + radius, ground, z], apex, near);
    pushTriangle(vertices, [x, ground, z - radius], [x, ground, z + radius], apex, near);
    pushTriangle(vertices, [x - radius, ground, z], apex, [x, ground, z + radius], near);
    pushTriangle(vertices, [x + radius, ground, z], [x, ground, z - radius], apex, near);
  }

  // The orb: an octahedron, eight triangles, scaled by the breath at draw time.
  const [ox, oy, oz] = ORB_CENTER;
  const r = 0.2;
  const up: Vec3 = [ox, oy + r, oz];
  const down: Vec3 = [ox, oy - r, oz];
  const ring: Vec3[] = [
    [ox + r, oy, oz],
    [ox, oy, oz - r],
    [ox - r, oy, oz],
    [ox, oy, oz + r],
  ];
  for (let i = 0; i < ring.length; i += 1) {
    const a = ring[i] as Vec3;
    const b = ring[(i + 1) % ring.length] as Vec3;
    pushTriangle(vertices, a, b, up, CALM_PALETTE.orb, true);
    pushTriangle(vertices, b, a, down, CALM_PALETTE.orbEdge, true);
  }

  return vertices;
}

export interface ViewMatrices {
  projectionMatrix: Float32Array;
  transform: { inverse: { matrix: Float32Array } };
}

export interface Viewport {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface CalmWebGL {
  /** Triangles in the scene, pinned by the budget test. */
  triangleCount: number;
  draw(
    view: ViewMatrices,
    viewport: Viewport,
    orbScale: number,
    framebuffer: WebGLFramebuffer | null,
  ): void;
  clear(): void;
  dispose(): void;
}

function compile(gl: WebGLRenderingContext, type: number, source: string): WebGLShader | null {
  const shader = gl.createShader(type);
  if (shader === null) {
    return null;
  }
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (gl.getShaderParameter(shader, gl.COMPILE_STATUS) !== true) {
    gl.deleteShader(shader);
    return null;
  }
  return shader;
}

/**
 * Create the renderer, or null where WebGL is unavailable or the shaders do
 * not compile. A null return is a supported outcome: the calm space falls back
 * to the 2D painter and says nothing alarming about it.
 */
export function createCalmWebGL(gl: WebGLRenderingContext): CalmWebGL | null {
  const vertex = compile(gl, gl.VERTEX_SHADER, VERTEX_SHADER);
  const fragment = compile(gl, gl.FRAGMENT_SHADER, FRAGMENT_SHADER);
  if (vertex === null || fragment === null) {
    if (vertex !== null) gl.deleteShader(vertex);
    if (fragment !== null) gl.deleteShader(fragment);
    return null;
  }
  const program = gl.createProgram();
  if (program === null) {
    gl.deleteShader(vertex);
    gl.deleteShader(fragment);
    return null;
  }
  gl.attachShader(program, vertex);
  gl.attachShader(program, fragment);
  gl.linkProgram(program);
  if (gl.getProgramParameter(program, gl.LINK_STATUS) !== true) {
    gl.deleteProgram(program);
    gl.deleteShader(vertex);
    gl.deleteShader(fragment);
    return null;
  }

  const positionLocation = gl.getAttribLocation(program, "aPosition");
  const colorLocation = gl.getAttribLocation(program, "aColor");
  const orbLocation = gl.getAttribLocation(program, "aOrb");
  const projectionLocation = gl.getUniformLocation(program, "uProjection");
  const viewLocation = gl.getUniformLocation(program, "uView");
  const orbCenterLocation = gl.getUniformLocation(program, "uOrbCenter");
  const orbScaleLocation = gl.getUniformLocation(program, "uOrbScale");

  const triangles = buildSceneTriangles();
  const data = new Float32Array(triangles.length * 7);
  triangles.forEach((vertexData, index) => {
    const offset = index * 7;
    const [r, g, b] = hexToRgb(vertexData.color);
    data[offset] = vertexData.position[0];
    data[offset + 1] = vertexData.position[1];
    data[offset + 2] = vertexData.position[2];
    data[offset + 3] = r;
    data[offset + 4] = g;
    data[offset + 5] = b;
    data[offset + 6] = vertexData.orb === true ? 1 : 0;
  });

  const buffer = gl.createBuffer();
  if (buffer === null) {
    gl.deleteProgram(program);
    gl.deleteShader(vertex);
    gl.deleteShader(fragment);
    return null;
  }
  gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
  gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW);

  let disposed = false;

  return {
    triangleCount: data.length / 7 / 3,
    draw(view, viewport, orbScale, framebuffer) {
      if (disposed) {
        return;
      }
      gl.bindFramebuffer(gl.FRAMEBUFFER, framebuffer);
      gl.viewport(viewport.x, viewport.y, viewport.width, viewport.height);
      // One constant clear colour for the whole session: no strobe, no fade.
      gl.clearColor(0.149, 0.219, 0.29, 1);
      gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
      gl.useProgram(program);
      gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
      const stride = 7 * 4;
      gl.enableVertexAttribArray(positionLocation);
      gl.vertexAttribPointer(positionLocation, 3, gl.FLOAT, false, stride, 0);
      gl.enableVertexAttribArray(colorLocation);
      gl.vertexAttribPointer(colorLocation, 3, gl.FLOAT, false, stride, 3 * 4);
      gl.enableVertexAttribArray(orbLocation);
      gl.vertexAttribPointer(orbLocation, 1, gl.FLOAT, false, stride, 6 * 4);
      gl.uniformMatrix4fv(projectionLocation, false, view.projectionMatrix);
      gl.uniformMatrix4fv(viewLocation, false, view.transform.inverse.matrix);
      gl.uniform3f(orbCenterLocation, ORB_CENTER[0], ORB_CENTER[1], ORB_CENTER[2]);
      const clamped = Number.isFinite(orbScale)
        ? Math.min(ORB_SCALE_MAX, Math.max(ORB_SCALE_MIN, orbScale))
        : ORB_SCALE_MIN;
      gl.uniform1f(orbScaleLocation, clamped);
      gl.drawArrays(gl.TRIANGLES, 0, data.length / 7);
    },
    clear() {
      gl.clearColor(0.149, 0.219, 0.29, 1);
      gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    },
    dispose() {
      if (disposed) {
        return;
      }
      disposed = true;
      gl.deleteBuffer(buffer);
      gl.deleteProgram(program);
      gl.deleteShader(vertex);
      gl.deleteShader(fragment);
    },
  };
}

/** Map the breathing curve onto the orb's world scale. */
export function orbScaleForBreath(scale: number): number {
  if (!Number.isFinite(scale)) {
    return ORB_SCALE_MIN;
  }
  const t = Math.min(1, Math.max(0, (scale - MIN_SCALE) / (1 - MIN_SCALE)));
  return ORB_SCALE_MIN + (ORB_SCALE_MAX - ORB_SCALE_MIN) * t;
}

/** The pinned triangle count, exported for the budget test. */
export const EXPECTED_TRIANGLES = WEBGL_TRIANGLE_BUDGET;
