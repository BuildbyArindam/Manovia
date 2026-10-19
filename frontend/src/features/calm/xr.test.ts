/**
 * WebXR entry, lifecycle and comfort contracts (Day 46).
 *
 * No headset exists in this sandbox, so the session is a hand-written fake that
 * records exactly what the code asks of it. What is proven here is the part
 * that matters for comfort and for the fallback:
 *
 * * feature detection answers "unavailable"/"unsupported" instead of throwing;
 * * the session is seated (`local`), with no other reference space and no
 *   locomotion input;
 * * every view in the viewer pose is drawn;
 * * one `select` (a single controller press) ends the session;
 * * cleanup happens once, and listeners do not leak.
 */

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it, vi } from "vitest";

import { createCalmWebGL } from "./renderWebGL";
import { createFakeGl } from "./testGl";
import {
  detectVRSupport,
  enterCalmXR,
  type XRLayerLike,
  type XRReferenceSpaceLike,
  type XRSessionLike,
  type XRSystemLike,
} from "./xr";

const HERE = dirname(fileURLToPath(import.meta.url));

interface FakeSession extends XRSessionLike {
  emit: (type: string) => void;
  endCalls: number;
  frameCallback: ((time: number, frame: unknown) => void) | null;
  referenceSpaces: string[];
  renderStates: unknown[];
  listeners: Map<string, Set<() => void>>;
}

function fakeSession(): FakeSession {
  const listeners = new Map<string, Set<() => void>>();
  let handle = 0;
  const session: FakeSession = {
    endCalls: 0,
    frameCallback: null,
    referenceSpaces: [],
    renderStates: [],
    listeners,
    addEventListener(type, listener) {
      const set = listeners.get(type) ?? new Set();
      set.add(listener);
      listeners.set(type, set);
    },
    removeEventListener(type, listener) {
      listeners.get(type)?.delete(listener);
    },
    emit(type) {
      for (const listener of [...(listeners.get(type) ?? [])]) {
        listener();
      }
    },
    async end() {
      session.endCalls += 1;
      session.emit("end");
    },
    updateRenderState(state) {
      session.renderStates.push(state);
    },
    async requestReferenceSpace(type: string): Promise<XRReferenceSpaceLike> {
      session.referenceSpaces.push(type);
      return {};
    },
    requestAnimationFrame(callback) {
      session.frameCallback = callback as (time: number, frame: unknown) => void;
      handle += 1;
      return handle;
    },
    cancelAnimationFrame() {
      session.frameCallback = null;
    },
    get renderState() {
      return { baseLayer: null };
    },
  };
  return session;
}

function fakeXR(session: FakeSession, supported = true): XRSystemLike & { calls: unknown[] } {
  const calls: unknown[] = [];
  return {
    calls,
    isSessionSupported: async (mode: string) => {
      calls.push(["support", mode]);
      return supported;
    },
    requestSession: async (mode: string, init?: Record<string, unknown>) => {
      calls.push(["session", mode, init]);
      return session;
    },
  };
}

function fakeLayer() {
  const Layer = class {
    framebuffer = null;
    getViewport() {
      return { x: 0, y: 0, width: 640, height: 720 };
    }
  };
  return {
    Layer: Layer as unknown as new (
      session: XRSessionLike,
      gl: WebGLRenderingContext,
    ) => XRLayerLike,
  };
}

describe("WebXR feature detection", () => {
  it("reports unavailable when the API is missing", async () => {
    await expect(detectVRSupport(null)).resolves.toBe("unavailable");
  });

  it("reports supported, unsupported and never throws", async () => {
    await expect(detectVRSupport(fakeXR(fakeSession(), true))).resolves.toBe("supported");
    await expect(detectVRSupport(fakeXR(fakeSession(), false))).resolves.toBe("unsupported");
    const broken: XRSystemLike = {
      isSessionSupported: async () => {
        throw new Error("permission denied");
      },
      requestSession: async () => {
        throw new Error("unreachable");
      },
    };
    await expect(detectVRSupport(broken)).resolves.toBe("unsupported");
  });
});

describe("an immersive calm session", () => {
  it("is seated, has no locomotion input, and draws every view", async () => {
    const session = fakeSession();
    const xr = fakeXR(session);
    const { Layer } = fakeLayer();
    const { gl, record } = createFakeGl();
    const onEnded = vi.fn();

    const handle = await enterCalmXR({ gl, xr, layerFactory: Layer, onEnded });
    expect(xr.calls[0]).toEqual(["session", "immersive-vr", undefined]);
    expect(session.referenceSpaces).toEqual(["local"]);
    expect(session.renderStates).toHaveLength(1);

    const getViewerPose = vi.fn(() => ({
      views: [
        {
          eye: "left",
          projectionMatrix: new Float32Array(16),
          transform: { inverse: { matrix: new Float32Array(16) } },
        },
        {
          eye: "right",
          projectionMatrix: new Float32Array(16),
          transform: { inverse: { matrix: new Float32Array(16) } },
        },
      ],
    }));
    session.frameCallback?.(1000, { getViewerPose });
    expect(getViewerPose).toHaveBeenCalledTimes(1);
    expect(record.draws).toBe(2);

    await handle.end();
    expect(session.endCalls).toBe(1);
    expect(onEnded).toHaveBeenCalledWith("page");
    await expect(handle.ended).resolves.toBeUndefined();
  });

  it("exits on a single controller press, once, and stops drawing", async () => {
    const session = fakeSession();
    const { Layer } = fakeLayer();
    const { gl, record } = createFakeGl();
    const onEnded = vi.fn();
    const handle = await enterCalmXR({
      gl,
      xr: fakeXR(session),
      layerFactory: Layer,
      onEnded,
    });

    session.emit("select");
    await handle.ended;
    expect(onEnded).toHaveBeenCalledTimes(1);
    expect(onEnded).toHaveBeenCalledWith("gesture");
    expect(session.endCalls).toBe(1);
    // The listeners are gone and no further frame is scheduled.
    expect(session.listeners.get("select")?.size ?? 0).toBe(0);
    expect(session.listeners.get("squeeze")?.size ?? 0).toBe(0);

    const before = record.draws;
    session.frameCallback?.(2000, { getViewerPose: () => ({ views: [] }) });
    expect(record.draws).toBe(before);
  });

  it("fails as a caught error when WebXR or the layer is missing", async () => {
    const { gl } = createFakeGl();
    await expect(enterCalmXR({ gl, xr: null, layerFactory: null })).rejects.toThrow(/WebXR/);

    const session = fakeSession();
    await expect(enterCalmXR({ gl, xr: fakeXR(session), layerFactory: null })).rejects.toThrow(
      /XRWebGLLayer/,
    );

    // A session that ends before the renderer exists is still closed.
    const { Layer } = fakeLayer();
    const noShader = createFakeGl({ failShaders: true });
    await expect(
      enterCalmXR({ gl: noShader.gl, xr: fakeXR(session), layerFactory: Layer }),
    ).rejects.toThrow(/could not be drawn/);
    expect(session.endCalls).toBe(1);
  });

  it("has no artificial locomotion anywhere in the XR source", () => {
    const source = readFileSync(join(HERE, "xr.ts"), "utf8")
      .replace(/\/\*[\s\S]*?\*\//g, " ")
      .replace(/\/\/.*$/gm, " ");
    expect(source).toContain('requestSession("immersive-vr")');
    expect(source).toContain('requestReferenceSpace("local")');
    expect(source).not.toContain("local-floor");
    expect(source).not.toContain("bounded-floor");
    expect(source).not.toMatch(/axes|gamepad|joystick|teleport/i);
    // There is no camera position to animate: only the headset's pose.
    expect(source).not.toMatch(/pose\.transform\.position/);
  });
});

describe("the renderer the XR session uses", () => {
  it("is the same three dozen triangles as the pinned budget", () => {
    const { gl } = createFakeGl();
    expect(createCalmWebGL(gl)?.triangleCount).toBe(36);
  });
});
