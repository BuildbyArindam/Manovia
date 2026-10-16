/**
 * The biofeedback session controller (Day 42).
 *
 * Owns the pacer, the in-memory RR buffer, the rolling signal-quality state
 * and the session summary — and nothing else. Device readings and CSV imports
 * both arrive through `ingest()`/`importSamples()`, so both paths share one
 * pipeline:
 *
 *   readings -> RRStreamAccumulator (or imported samples) -> rejectArtefacts
 *            -> slidingWindowMetrics (quality gate inside) -> UI
 *
 * **Data stays local.** The RR buffer lives in this controller's memory for
 * the length of the session and is dropped when the session ends. Only an
 * explicit "save summary" persists anything, encrypted
 * (`store/summaryStore.ts`). Raw intervals are never written anywhere.
 *
 * The loop is driven by explicit `tick()` calls (a timer in the UI), so tests
 * control time exactly.
 *
 * WELLNESS FEATURE. Not a medical device.
 */

import { useCallback, useMemo, useRef, useState } from "react";

import { RRStreamAccumulator } from "../signal/rr";
import { rejectArtefacts } from "../signal/artefact";
import { DEFAULT_WINDOW_OPTIONS, slidingWindowMetrics } from "../signal/metrics";
import { assessQuality } from "../signal/quality";
import type { CleanSeries, RRSample, WindowMetrics } from "../signal/types";
import { BreathingPacer, type PacerState } from "../pacer/pacer";
import {
  summarizeSession,
  type MoodRating,
  type SessionStopReason,
  type SessionSummary,
} from "./summary";
import type { HeartRateReading } from "../ble/heartRateDevice";

export type SessionPhase = "idle" | "running" | "ended";

export interface BiofeedbackSessionState {
  phase: SessionPhase;
  pacer: PacerState;
  /** Metrics for the most recent complete window, after the quality gate. */
  current: WindowMetrics | null;
  /** Every window computed so far (for the summary). */
  windows: WindowMetrics[];
  preMood: MoodRating | null;
  postMood: MoodRating | null;
  /** Latest quality assessment over the trailing window. */
  quality: { level: "good" | "fair" | "poor"; score: number };
  /** Beats accepted / rejected so far, for honest display. */
  counts: { accepted: number; rejected: number; dropouts: number };
  summary: SessionSummary | null;
  /** Session data source, recorded in the summary. */
  source: "wearable" | "csv" | null;
  paceRateStartBpm: number;
}

export interface IngestResult {
  samplesAdded: number;
  malformedPackets: number;
}

/** The tick cadence the UI runs at. */
export const TICK_MS = 250;
/** Windows are recomputed at most this often (and on every tick boundary). */
export const WINDOW_RECOMPUTE_EVERY_MS = 5_000;

export interface BiofeedbackSessionController {
  readonly state: BiofeedbackSessionState;
  /** Feed live sensor readings (BLE notifications). */
  ingest(readings: readonly HeartRateReading[], nowMs: number): IngestResult;
  /** Feed an imported CSV series (the no-hardware path). */
  importSamples(samples: readonly RRSample[], nowMs: number): void;
  /** Advance the clock; call every {@link TICK_MS} while a session runs. */
  tick(nowMs: number): void;
  start(preMood: MoodRating | null, nowMs: number, source: "wearable" | "csv"): void;
  end(postMood: MoodRating | null, reason: SessionStopReason, nowMs: number): SessionSummary | null;
  dizzy(nowMs: number): SessionSummary | null;
  setManualRate(rateBpm: number): void;
  resumeAuto(): void;
  setPreMood(mood: MoodRating | null): void;
  /** Drop the ended session (back to idle). */
  reset(): void;
}

/**
 * Create the controller. Framework-free on purpose (no React inside), so the
 * session behaviour is testable with explicit time.
 */
export function createBiofeedbackSession(): BiofeedbackSessionController {
  const pacer = new BreathingPacer();
  const accumulator = new RRStreamAccumulator();
  let imported: RRSample[] | null = null;
  let phase: SessionPhase = "idle";
  let preMood: MoodRating | null = null;
  let postMood: MoodRating | null = null;
  let source: BiofeedbackSessionState["source"] = null;
  let summary: SessionSummary | null = null;
  let windows: WindowMetrics[] = [];
  let current: WindowMetrics | null = null;
  let quality: BiofeedbackSessionState["quality"] = { level: "poor", score: 0 };
  let counts = { accepted: 0, rejected: 0, dropouts: 0 };
  let lastWindowComputeMs = -Infinity;
  let sessionStartMs = 0;
  let sessionEndedMs = 0;
  let paceRateStartBpm = pacer.state().rateBpm;

  const currentSamples = (): RRSample[] => imported ?? accumulator.series();

  const snapshot = (): BiofeedbackSessionState => ({
    phase,
    pacer: pacer.state(),
    current,
    windows,
    preMood,
    postMood,
    quality,
    counts,
    summary,
    source,
    paceRateStartBpm,
  });

  let state: BiofeedbackSessionState = snapshot();

  const recompute = (nowMs: number): void => {
    const samples = currentSamples();
    const series: CleanSeries = rejectArtefacts(samples);
    windows = slidingWindowMetrics(series, {
      ...DEFAULT_WINDOW_OPTIONS,
      contactDetected: null,
    });
    current = windows.length > 0 ? (windows[windows.length - 1] ?? null) : null;

    const inWindow = <T extends { tMs: number }>(items: readonly T[]): T[] =>
      items.filter((s) => s.tMs > nowMs - DEFAULT_WINDOW_OPTIONS.windowMs && s.tMs <= nowMs);
    const gapMs = series.gaps
      .filter((g) => g.tAfterMs > nowMs - DEFAULT_WINDOW_OPTIONS.windowMs && g.tAfterMs <= nowMs)
      .reduce((acc, g) => acc + g.gapMs, 0);
    quality = assessQuality({
      acceptedBeats: inWindow(series.accepted).length,
      rejectedBeats: inWindow(series.rejected).length,
      gapMs,
      windowMs: DEFAULT_WINDOW_OPTIONS.windowMs,
      contactDetected: null,
      minBeats: DEFAULT_WINDOW_OPTIONS.minBeats,
    });
    counts = {
      accepted: series.accepted.length,
      rejected: series.rejected.length,
      dropouts: series.gaps.length,
    };
    lastWindowComputeMs = nowMs;
    state = snapshot();
  };

  return {
    get state() {
      return state;
    },

    ingest(readings, nowMs) {
      let samplesAdded = 0;
      let malformedPackets = 0;
      if (imported !== null) {
        // A live sensor replaces the import for the live path.
        imported = null;
      }
      for (const reading of readings) {
        if (reading.malformed) {
          malformedPackets += 1;
          continue;
        }
        samplesAdded += accumulator.push(reading.rrIntervalsMs, reading.receivedAtMs).length;
      }
      if (phase === "running" && samplesAdded > 0) {
        recompute(nowMs);
      } else {
        state = snapshot();
      }
      return { samplesAdded, malformedPackets };
    },

    importSamples(samples, nowMs) {
      imported = samples.map((s, i) => ({ ...s, rawIndex: i }));
      if (phase === "running") {
        recompute(nowMs);
      } else {
        state = snapshot();
      }
    },

    tick(nowMs) {
      if (phase !== "running") {
        return;
      }
      pacer.advance(TICK_MS);
      if (nowMs - lastWindowComputeMs >= WINDOW_RECOMPUTE_EVERY_MS) {
        recompute(nowMs);
        const series = rejectArtefacts(currentSamples());
        pacer.adapt(series.accepted, quality.level, nowMs);
      }
      state = snapshot();
    },

    start(preMoodValue, nowMs, sourceValue) {
      preMood = preMoodValue;
      source = sourceValue;
      sessionStartMs = nowMs;
      sessionEndedMs = 0;
      phase = "running";
      summary = null;
      windows = [];
      current = null;
      quality = { level: "poor", score: 0 };
      lastWindowComputeMs = -Infinity;
      paceRateStartBpm = pacer.state().rateBpm;
      state = snapshot();
    },

    end(postMoodValue, reason, nowMs) {
      postMood = postMoodValue;
      // Re-ending (e.g. picking a mood afterwards) keeps the original end
      // time: the session ended when it ended.
      const endedAtMs = phase === "ended" && sessionEndedMs > 0 ? sessionEndedMs : nowMs;
      sessionEndedMs = endedAtMs;
      phase = "ended";
      if (reason === "dizzy") {
        pacer.stop("dizzy");
      } else {
        pacer.stop("user");
      }
      recompute(endedAtMs);
      summary = summarizeSession({
        startedAtMs: sessionStartMs,
        endedAtMs,
        windows,
        preMood,
        postMood: postMoodValue,
        paceRateStartBpm,
        paceRateEndBpm: pacer.state().rateBpm,
        autoAdjust: pacer.state().autoAdjust,
        stopReason: reason,
      });
      state = snapshot();
      return summary;
    },

    dizzy(nowMs) {
      return this.end(postMood, "dizzy", nowMs);
    },

    setManualRate(rateBpm) {
      pacer.setManualRate(rateBpm);
      state = snapshot();
    },

    resumeAuto() {
      pacer.resumeAuto();
      state = snapshot();
    },

    setPreMood(mood) {
      preMood = mood;
      state = snapshot();
    },

    reset() {
      phase = "idle";
      sessionEndedMs = 0;
      summary = null;
      windows = [];
      current = null;
      preMood = null;
      postMood = null;
      source = null;
      quality = { level: "poor", score: 0 };
      counts = { accepted: 0, rejected: 0, dropouts: 0 };
      state = snapshot();
    },
  };
}

export interface BiofeedbackSessionHandle extends BiofeedbackSessionController {
  /** Force a re-render after imperative calls. */
  bump(): void;
}

/**
 * React binding. The returned object delegates every access to the live
 * controller (so `state` is never a stale copy) and bumps the component when
 * the caller mutates anything.
 */
export function useBiofeedbackSession(): BiofeedbackSessionHandle {
  const controllerRef = useRef<BiofeedbackSessionController | null>(null);
  if (controllerRef.current === null) {
    controllerRef.current = createBiofeedbackSession();
  }
  const controller = controllerRef.current;
  const [, setVersion] = useState(0);
  const bump = useCallback(() => setVersion((v) => v + 1), []);

  return useMemo(() => {
    const wrap = <A extends unknown[], R>(fn: (...args: A) => R) =>
      (...args: A): R => {
        const result = fn(...args);
        bump();
        return result;
      };
    return {
      get state() {
        return controller.state;
      },
      ingest: wrap(controller.ingest.bind(controller)),
      importSamples: wrap(controller.importSamples.bind(controller)),
      tick: wrap(controller.tick.bind(controller)),
      start: wrap(controller.start.bind(controller)),
      end: wrap(controller.end.bind(controller)),
      dizzy: wrap(controller.dizzy.bind(controller)),
      setManualRate: wrap(controller.setManualRate.bind(controller)),
      resumeAuto: wrap(controller.resumeAuto.bind(controller)),
      setPreMood: wrap(controller.setPreMood.bind(controller)),
      reset: wrap(controller.reset.bind(controller)),
      bump,
    };
  }, [controller, bump]);
}
