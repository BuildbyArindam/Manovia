/**
 * The calm space (Day 46).
 *
 * A seated, still place: a low-poly evening lake, a soft orb that follows a
 * six-breaths-a-minute cycle, optional procedural audio, and a way into VR when
 * the browser and a headset can actually do it.
 *
 * ## Comfort rules, and where they live
 *
 * * **No artificial locomotion.** The 2D painter never pans or scrolls; the XR
 *   renderer uses the headset pose and nothing else. There is no speed, no
 *   joystick, no teleport.
 * * **Stable horizon.** `HORIZON_FRACTION` and the ground plane are constants.
 * * **No flashing.** Colours are constant; only the orb's size changes, on a
 *   ten-second cosine. There is no strobe, no fade-to-white, no camera shake.
 * * **No sudden motion.** The breath curve has zero slope at both ends, and
 *   `motion: none` (and the OS reduced-motion setting) draws one static frame.
 * * **One-gesture exit from VR.** Any `select`/`squeeze` on a controller ends
 *   the session; the page also has a "Leave VR" button.
 * * **Ten-minute break prompt.** A non-blocking reminder; in VR it returns the
 *   person to the page so the prompt is visible rather than unheard.
 * * **A clear note about who VR is not recommended for**, before the button.
 * * **A full 2D fallback.** No WebXR, no WebGL, no audio — this page still
 *   works, and says why it is in 2D without treating that as a failure.
 *
 * Wellness feature. Not a medical device.
 */

import { useCallback, useEffect, useRef, useState, type ReactElement } from "react";
import { Link } from "react-router-dom";

import { t } from "@/lib/i18n/i18n";

import { CalmAudio } from "./audio";
import { breathAt } from "./breathing";
import { drawCalmFrame } from "./render2d";
import { useSensory } from "@/features/sensory/SensoryProvider";
import { detectVRSupport, enterCalmXR, type XRSessionHandle, type XRSupport } from "./xr";

export const BREAK_AFTER_MS = 10 * 60 * 1000;
/** The label updates at this rate; the canvas itself can run at 60 fps. */
const LABEL_INTERVAL_MS = 250;
const BREAK_CHECK_MS = 15_000;
const MAX_DPR = 2;

type Status = "idle" | "running" | "paused";

export function CalmSpace(): ReactElement {
  const { motion, sound: soundSetting } = useSensory();
  const staticOnly = motion === "none";

  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const glCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const elapsedRef = useRef(0);
  const lastFrameRef = useRef<number | null>(null);
  const rafRef = useRef<number | null>(null);
  const labelAtRef = useRef(0);
  const audioRef = useRef<CalmAudio | null>(null);
  const xrRef = useRef<XRSessionHandle | null>(null);

  const [status, setStatus] = useState<Status>("idle");
  const [phase, setPhase] = useState<"inhale" | "exhale">("inhale");
  const [xrSupport, setXrSupport] = useState<XRSupport | "checking">("checking");
  const [xrActive, setXrActive] = useState(false);
  const [xrMessage, setXrMessage] = useState<string | null>(null);
  const [soundWanted, setSoundWanted] = useState(false);
  const [soundRunning, setSoundRunning] = useState(false);
  const [breakDue, setBreakDue] = useState(false);
  const startedAtRef = useRef<number | null>(null);

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    if (canvas === null) {
      return;
    }
    const context = canvas.getContext("2d");
    if (context === null) {
      return;
    }
    const cssWidth = Math.max(1, canvas.clientWidth || 360);
    const cssHeight = Math.max(1, canvas.clientHeight || 260);
    const dpr = Math.min(MAX_DPR, typeof window === "undefined" ? 1 : window.devicePixelRatio || 1);
    const width = Math.round(cssWidth * dpr);
    const height = Math.round(cssHeight * dpr);
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }
    drawCalmFrame(context as unknown as Parameters<typeof drawCalmFrame>[0], {
      width,
      height,
      elapsedMs: elapsedRef.current,
      animateOrb: !staticOnly,
    });
  }, [staticOnly]);

  // The animation loop. Under `motion: none` (or the OS setting) it draws one
  // static frame and schedules nothing — the label still changes in text, so
  // the cue is never lost.
  useEffect(() => {
    if (status !== "running") {
      lastFrameRef.current = null;
      draw();
      return;
    }
    if (staticOnly) {
      draw();
      return;
    }
    let cancelled = false;
    const tick = (now: number): void => {
      if (cancelled) {
        return;
      }
      if (lastFrameRef.current !== null) {
        elapsedRef.current += Math.min(100, Math.max(0, now - lastFrameRef.current));
      }
      lastFrameRef.current = now;
      draw();
      if (now - labelAtRef.current >= LABEL_INTERVAL_MS) {
        labelAtRef.current = now;
        const breath = breathAt(elapsedRef.current);
        setPhase(breath.phase);
        audioRef.current?.setBreathScale(breath.scale);
      }
      rafRef.current = window.requestAnimationFrame(tick);
    };
    rafRef.current = window.requestAnimationFrame(tick);
    return () => {
      cancelled = true;
      if (rafRef.current !== null) {
        window.cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
      lastFrameRef.current = null;
    };
  }, [status, staticOnly, draw]);

  // Text cue while the orb is static (and as a second channel while it moves).
  useEffect(() => {
    if (status !== "running" || !staticOnly) {
      return;
    }
    const interval = window.setInterval(() => {
      elapsedRef.current += LABEL_INTERVAL_MS;
      const breath = breathAt(elapsedRef.current);
      setPhase(breath.phase);
      draw();
    }, LABEL_INTERVAL_MS);
    return () => {
      window.clearInterval(interval);
    };
  }, [status, staticOnly, draw]);

  // Redraw on a size change (rotation, window resize, zoom).
  useEffect(() => {
    const onResize = (): void => {
      draw();
    };
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
    };
  }, [draw]);

  // Feature detection is asynchronous and never throws.
  useEffect(() => {
    let cancelled = false;
    void detectVRSupport().then((support) => {
      if (!cancelled) {
        setXrSupport(support);
      }
    });
    return () => {
      cancelled = true;
    };
  }, []);

  // The ten-minute reminder.
  useEffect(() => {
    if (status !== "running") {
      return;
    }
    const interval = window.setInterval(() => {
      if (startedAtRef.current === null) {
        return;
      }
      if (performance.now() - startedAtRef.current >= BREAK_AFTER_MS) {
        setBreakDue(true);
      }
    }, BREAK_CHECK_MS);
    return () => {
      window.clearInterval(interval);
    };
  }, [status]);

  // Sound follows the running state; the graph is created only on request.
  useEffect(() => {
    if (status === "running" && soundWanted && soundSetting === "low") {
      if (audioRef.current === null) {
        audioRef.current = new CalmAudio();
      }
      const started = audioRef.current.start();
      setSoundRunning(started);
      return;
    }
    audioRef.current?.stop();
    setSoundRunning(false);
  }, [status, soundWanted, soundSetting]);

  // Everything stops when the page is left, including an open XR session.
  useEffect(() => {
    return () => {
      audioRef.current?.stop();
      audioRef.current = null;
      if (rafRef.current !== null) {
        window.cancelAnimationFrame(rafRef.current);
      }
      void xrRef.current?.end();
      xrRef.current = null;
    };
  }, []);

  const start = (): void => {
    startedAtRef.current = performance.now();
    setStatus("running");
  };

  const pause = (): void => {
    setStatus("paused");
    // Keep the wall clock honest: a pause is not a break from the 10-minute
    // reminder, but the timer restarts when the person resumes.
    startedAtRef.current = null;
  };

  const resume = (): void => {
    startedAtRef.current = performance.now() - elapsedRef.current;
    setStatus("running");
  };

  const leaveBreak = (): void => {
    setBreakDue(false);
    startedAtRef.current = performance.now();
  };

  const enterVR = async (): Promise<void> => {
    setXrMessage(null);
    const glCanvas = glCanvasRef.current;
    if (glCanvas === null) {
      return;
    }
    const gl = glCanvas.getContext("webgl", { antialias: false, alpha: false });
    if (gl === null) {
      setXrMessage(t("calm.xr.noWebGL"));
      return;
    }
    try {
      const handle = await enterCalmXR({
        gl,
        onEnded: () => {
          setXrActive(false);
          xrRef.current = null;
          setXrMessage(t("calm.xr.ended"));
        },
      });
      xrRef.current = handle;
      setXrActive(true);
      setXrMessage(t("calm.xr.inside"));
    } catch {
      setXrMessage(t("calm.xr.failed"));
    }
  };

  const leaveVR = async (): Promise<void> => {
    await xrRef.current?.end();
    xrRef.current = null;
    setXrActive(false);
  };

  // A break prompt that cannot be seen in a headset is not a prompt.
  useEffect(() => {
    if (breakDue && xrActive) {
      void leaveVR();
    }
    // leaveVR is stable enough for this one-shot safety behaviour.
  }, [breakDue, xrActive]);

  const breakNow = (): void => {
    setBreakDue(false);
    pause();
  };

  return (
    <div className="space-y-6" data-testid="calm-space">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl">{t("calm.title")}</h1>
          <p className="mt-1 text-sm text-ink-muted">{t("calm.lede")}</p>
        </div>
        <Link
          to="/"
          className="inline-flex min-h-11 items-center rounded-xl border border-border-strong px-4 py-2 text-sm font-semibold text-ink"
        >
          {t("calm.leave")}
        </Link>
      </div>

      <section
        aria-labelledby="calm-scene-heading"
        className="overflow-hidden rounded-2xl border border-border bg-surface shadow-soft"
      >
        <h2 id="calm-scene-heading" className="sr-only">
          {t("calm.sceneHeading")}
        </h2>
        <canvas
          ref={canvasRef}
          data-testid="calm-canvas"
          aria-hidden="true"
          className="block h-64 w-full sm:h-80"
        />
        <p className="sr-only">{t("calm.sceneDescription")}</p>

        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border p-4">
          <p className="text-lg font-semibold" data-testid="calm-phase" aria-live="off">
            {status === "running"
              ? phase === "inhale"
                ? t("calm.breath.in")
                : t("calm.breath.out")
              : t("calm.breath.rest")}
          </p>
          <div className="flex flex-wrap gap-2">
            {status === "idle" ? (
              <button
                type="button"
                onClick={start}
                data-testid="calm-start"
                className="inline-flex min-h-11 items-center rounded-xl bg-accent-bg px-4 py-2 text-sm font-semibold text-accent-fg"
              >
                {t("calm.start")}
              </button>
            ) : null}
            {status === "running" ? (
              <button
                type="button"
                onClick={pause}
                data-testid="calm-pause"
                className="inline-flex min-h-11 items-center rounded-xl border border-border-strong px-4 py-2 text-sm font-semibold text-ink"
              >
                {t("calm.pause")}
              </button>
            ) : null}
            {status === "paused" ? (
              <button
                type="button"
                onClick={resume}
                data-testid="calm-resume"
                className="inline-flex min-h-11 items-center rounded-xl bg-accent-bg px-4 py-2 text-sm font-semibold text-accent-fg"
              >
                {t("calm.resume")}
              </button>
            ) : null}
            <button
              type="button"
              aria-pressed={soundWanted && soundRunning}
              disabled={soundSetting === "off"}
              onClick={() => {
                setSoundWanted((wanted) => !wanted);
              }}
              data-testid="calm-sound"
              className="inline-flex min-h-11 items-center rounded-xl border border-border-strong px-4 py-2 text-sm font-semibold text-ink disabled:cursor-not-allowed disabled:opacity-60"
            >
              {soundWanted && soundRunning ? t("calm.soundOff") : t("calm.soundOn")}
            </button>
          </div>
        </div>
        {soundSetting === "off" ? (
          <p
            className="border-t border-border px-4 py-2 text-sm text-ink-muted"
            data-testid="calm-sound-off-note"
          >
            {t("calm.soundDisabled")}
          </p>
        ) : null}
        {staticOnly ? (
          <p
            className="border-t border-border px-4 py-2 text-sm text-ink-muted"
            data-testid="calm-static-note"
          >
            {t("calm.staticNote")}
          </p>
        ) : null}
      </section>

      {breakDue ? (
        <section
          role="status"
          data-testid="calm-break-prompt"
          aria-labelledby="calm-break-heading"
          className="rounded-2xl border border-border-strong bg-surface-muted p-4"
        >
          <h2 id="calm-break-heading" className="text-lg">
            {t("calm.break.heading")}
          </h2>
          <p className="mt-1 text-sm text-ink-muted">{t("calm.break.body")}</p>
          <div className="mt-3 flex flex-wrap gap-2">
            <button
              type="button"
              onClick={breakNow}
              className="rounded-xl bg-accent-bg px-4 py-2 text-sm font-semibold text-accent-fg"
            >
              {t("calm.break.take")}
            </button>
            <button
              type="button"
              onClick={leaveBreak}
              className="rounded-xl border border-border-strong px-4 py-2 text-sm font-semibold text-ink"
            >
              {t("calm.break.stay")}
            </button>
          </div>
        </section>
      ) : null}

      <section
        aria-labelledby="calm-vr-heading"
        className="rounded-2xl border border-border bg-surface p-6 shadow-soft"
        data-testid="calm-vr-section"
      >
        <h2 id="calm-vr-heading" className="text-lg">
          {t("calm.xr.heading")}
        </h2>
        <p className="mt-1 text-sm text-ink-muted">{t("calm.xr.body")}</p>

        {xrSupport === "supported" ? (
          <div className="mt-3 flex flex-wrap items-center gap-3">
            {xrActive ? (
              <button
                type="button"
                onClick={() => {
                  void leaveVR();
                }}
                className="inline-flex min-h-11 items-center rounded-xl bg-accent-bg px-4 py-2 text-sm font-semibold text-accent-fg"
              >
                {t("calm.xr.leave")}
              </button>
            ) : (
              <button
                type="button"
                onClick={() => {
                  void enterVR();
                }}
                data-testid="calm-enter-vr"
                className="inline-flex min-h-11 items-center rounded-xl bg-accent-bg px-4 py-2 text-sm font-semibold text-accent-fg"
              >
                {t("calm.xr.enter")}
              </button>
            )}
            <p className="text-sm text-ink-muted">{t("calm.xr.gesture")}</p>
          </div>
        ) : null}

        {xrSupport !== "supported" && xrSupport !== "checking" ? (
          <p
            data-testid="calm-xr-fallback"
            className="mt-3 rounded-xl border border-border bg-bg p-3 text-sm"
          >
            {t("calm.xr.fallback")}
          </p>
        ) : null}

        {xrMessage !== null ? (
          <p role="status" data-testid="calm-xr-message" className="mt-3 text-sm text-ink-muted">
            {xrMessage}
          </p>
        ) : null}

        <canvas ref={glCanvasRef} className="hidden" aria-hidden="true" />

        <div className="mt-4 rounded-xl border border-border bg-bg p-4">
          <h3 className="text-base font-semibold">{t("calm.safety.heading")}</h3>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-ink-muted">
            <li>{t("calm.safety.notRecommended")}</li>
            <li>{t("calm.safety.seated")}</li>
            <li>{t("calm.safety.stop")}</li>
            <li>{t("calm.safety.notTherapy")}</li>
          </ul>
        </div>
      </section>
    </div>
  );
}
