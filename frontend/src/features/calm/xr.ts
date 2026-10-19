/**
 * WebXR entry and session lifecycle (Day 46).
 *
 * Feature detection is explicit and never assumes: `navigator.xr` may be
 * missing, may exist but refuse `isSessionSupported` (desktop Chrome without a
 * headset), or may reject because the page is not a secure context. All three
 * mean the same thing to the reader — "VR is not available here; the 2D calm
 * space is" — and none of them throws into React.
 *
 * The session is seated (`local` reference space), has no artificial
 * locomotion, and ends on a single controller gesture (`select` or `squeeze`).
 * That last rule is deliberately blunt: in a scene with no interactions, the
 * trigger is not doing anything else, and "press anything once to leave" is
 * easier to remember under stress than a menu.
 */

import { breathAt } from "./breathing";
import {
  createCalmWebGL,
  orbScaleForBreath,
  type CalmWebGL,
  type ViewMatrices,
} from "./renderWebGL";

export interface XRViewportLike {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface XRViewLike extends ViewMatrices {
  eye?: string;
}

export interface XRPoseLike {
  views: XRViewLike[];
}

export interface XRFrameLike {
  getViewerPose(referenceSpace: unknown): XRPoseLike | null;
}

export interface XRReferenceSpaceLike {
  readonly type?: string;
}

export interface XRLayerLike {
  framebuffer: WebGLFramebuffer | null;
  getViewport(view: XRViewLike): XRViewportLike;
}

export interface XRRenderStateLike {
  baseLayer: XRLayerLike | null;
}

export interface XRSessionLike {
  addEventListener(type: string, listener: () => void): void;
  removeEventListener(type: string, listener: () => void): void;
  end(): Promise<void>;
  updateRenderState(state: { baseLayer: XRLayerLike }): Promise<void> | void;
  requestReferenceSpace(type: string): Promise<XRReferenceSpaceLike>;
  requestAnimationFrame(callback: (time: number, frame: XRFrameLike) => void): number;
  cancelAnimationFrame(handle: number): void;
  readonly renderState: XRRenderStateLike;
  readonly inputSources?: unknown[];
}

export interface XRSystemLike {
  isSessionSupported(mode: string): Promise<boolean>;
  requestSession(mode: string, init?: Record<string, unknown>): Promise<XRSessionLike>;
}

export interface XRLayerFactory {
  new (session: XRSessionLike, gl: WebGLRenderingContext): XRLayerLike;
}

export interface XRSessionHandle {
  /** End the session from the page (the "Leave VR" button). Safe to call twice. */
  end: () => Promise<void>;
  /** Resolves when the session's `end` event has run the cleanup. */
  readonly ended: Promise<void>;
}

export interface EnterCalmXROptions {
  gl: WebGLRenderingContext;
  xr?: XRSystemLike | null;
  layerFactory?: XRLayerFactory | null;
  /** Called once, after the session has ended for any reason. */
  onEnded?: (reason: "gesture" | "page" | "session") => void;
  /** Called when the session could not be started; the 2D space stays up. */
  onError?: (error: Error) => void;
  /** Injectable for tests; defaults to the real clock. */
  now?: () => number;
}

export type XRSupport = "supported" | "unsupported" | "unavailable";

function xrSystemFromNavigator(): XRSystemLike | null {
  if (typeof navigator === "undefined") {
    return null;
  }
  const candidate = (navigator as Navigator & { xr?: XRSystemLike }).xr;
  return candidate ?? null;
}

function layerFactoryFromWindow(): XRLayerFactory | null {
  if (typeof window === "undefined") {
    return null;
  }
  const candidate = (window as Window & { XRWebGLLayer?: XRLayerFactory }).XRWebGLLayer;
  return candidate ?? null;
}

/**
 * Ask the browser whether immersive VR can start. Never rejects.
 *
 * `unsupported` means the API answered no (no headset, or the mode is not
 * offered); `unavailable` means there is no WebXR API at all. The UI says the
 * same thing for both, but the distinction is useful in diagnostics and tests.
 */
export async function detectVRSupport(
  xr: XRSystemLike | null = xrSystemFromNavigator(),
): Promise<XRSupport> {
  if (xr === null) {
    return "unavailable";
  }
  try {
    return (await xr.isSessionSupported("immersive-vr")) ? "supported" : "unsupported";
  } catch {
    return "unsupported";
  }
}

/**
 * Start an immersive session and drive the calm scene until it ends.
 *
 * Throws only when the caller explicitly wants to know (the UI catches and
 * keeps the 2D space). Every listener and frame handle is removed exactly once;
 * a second `end()` is a no-op rather than a second teardown.
 */
export async function enterCalmXR(options: EnterCalmXROptions): Promise<XRSessionHandle> {
  const xr = options.xr ?? xrSystemFromNavigator();
  const layerFactory = options.layerFactory ?? layerFactoryFromWindow();
  if (xr === null) {
    throw new Error("WebXR is not available in this browser.");
  }
  if (layerFactory === null) {
    throw new Error("XRWebGLLayer is not available in this browser.");
  }

  const session = await xr.requestSession("immersive-vr");
  const layer = new layerFactory(session, options.gl);
  await session.updateRenderState({ baseLayer: layer });
  // Seated. `local` is the one reference space an immersive session must
  // support; requesting `local-floor` here would be a standing assumption.
  const referenceSpace = await session.requestReferenceSpace("local");
  const renderer: CalmWebGL | null = createCalmWebGL(options.gl);
  if (renderer === null) {
    await session.end();
    throw new Error("The calm scene could not be drawn in VR.");
  }

  const now = options.now ?? (() => performance.now());
  const startedAt = now();
  let finished = false;
  let requestedReason: "gesture" | "page" | null = null;
  let frameHandle: number | null = null;
  let resolveEnded: () => void = () => undefined;
  const ended = new Promise<void>((resolve) => {
    resolveEnded = resolve;
  });

  const cleanup = (reason: "gesture" | "page" | "session"): void => {
    if (finished) {
      return;
    }
    finished = true;
    if (frameHandle !== null) {
      session.cancelAnimationFrame(frameHandle);
      frameHandle = null;
    }
    session.removeEventListener("select", onGesture);
    session.removeEventListener("squeeze", onGesture);
    session.removeEventListener("end", onSessionEnd);
    renderer.dispose();
    resolveEnded();
    options.onEnded?.(reason);
  };

  function onGesture(): void {
    if (finished) {
      return;
    }
    requestedReason = "gesture";
    void session
      .end()
      .catch(() => undefined)
      .finally(() => {
        cleanup(requestedReason ?? "gesture");
      });
  }

  function onSessionEnd(): void {
    cleanup(requestedReason ?? "session");
  }

  session.addEventListener("select", onGesture);
  session.addEventListener("squeeze", onGesture);
  session.addEventListener("end", onSessionEnd);

  const frame = (time: number, xrFrame: XRFrameLike): void => {
    if (finished) {
      return;
    }
    const pose = xrFrame.getViewerPose(referenceSpace);
    const baseLayer = session.renderState.baseLayer ?? layer;
    if (pose !== null) {
      // Only the headset's pose is used. There is no other camera input.
      const breath = breathAt(time - startedAt);
      const orbScale = orbScaleForBreath(breath.scale);
      for (const view of pose.views) {
        renderer.draw(view, baseLayer.getViewport(view), orbScale, baseLayer.framebuffer);
      }
    }
    frameHandle = session.requestAnimationFrame(frame);
  };

  try {
    frameHandle = session.requestAnimationFrame(frame);
  } catch (error) {
    cleanup("session");
    throw error instanceof Error ? error : new Error("The VR session could not start.");
  }

  return {
    end: async () => {
      if (finished) {
        return;
      }
      requestedReason = "page";
      try {
        await session.end();
      } catch {
        // The session may already have gone; cleanup still has to run.
      }
      cleanup(requestedReason ?? "page");
    },
    ended,
  };
}
