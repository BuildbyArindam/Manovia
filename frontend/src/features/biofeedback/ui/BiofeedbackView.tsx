/**
 * Biofeedback breathing with wearables (Day 42) — the whole surface.
 *
 * In the order a person meets it:
 *
 *   1. the wellness framing (this is NOT a medical device) and the privacy
 *      line (everything stays on this device unless a summary is explicitly
 *      saved, and saved summaries are encrypted);
 *   2. the sensor: Web Bluetooth connect with a permission explainer, or the
 *      honest "not supported on this browser" path with the CSV import;
 *   3. the pacer: the breathing cue, the adaptive rate (user-overridable),
 *      and the always-visible "I feel dizzy — stop" button;
 *   4. live numbers with the quality gate — numbers are withheld, not guessed,
 *      when the signal is poor;
 *   5. the session summary with caveats, and the trend over saved summaries.
 *
 * Data stays local: raw RR intervals live in memory for the session and are
 * dropped; only an explicit save writes the encrypted summary.
 *
 * WELLNESS FEATURE. Not a medical device, not a diagnosis, not a monitor.
 */

import { useCallback, useEffect, useMemo, useRef, useState, type ReactElement } from "react";

import { PageHeader } from "@/components/PageHeader";
import { t } from "@/lib/i18n/i18n";

import {
  UserCancelledError,
  webBluetoothSource,
  type ConnectionStatus,
  type HeartRateReading,
  type HeartRateSource,
} from "../ble/heartRateDevice";
import type { RRSample } from "../signal/types";
import { trendPoints, type MoodRating, type TrendPoint } from "../session/summary";
import { openSummaryStore } from "../store/summaryStore";
import { useBiofeedbackSession, TICK_MS } from "../session/useBiofeedbackSession";
import { CsvImportPanel } from "./CsvImportPanel";
import { DevicePanel } from "./DevicePanel";
import { PacerOrb } from "./PacerOrb";
import { SummaryPanel } from "./SummaryPanel";

export interface BiofeedbackViewProps {
  /** Injectable for tests (fake GATT); defaults to real Web Bluetooth. */
  source?: HeartRateSource;
  /** Injectable clock for tests. */
  now?: () => number;
}

const MOODS: readonly MoodRating[] = [1, 2, 3, 4, 5];

function MoodPicker({
  value,
  onChange,
  label,
  testId,
}: {
  value: MoodRating | null;
  onChange: (mood: MoodRating | null) => void;
  label: string;
  testId: string;
}): ReactElement {
  return (
    <fieldset className="space-y-2">
      <legend className="font-semibold">{label}</legend>
      <div className="flex flex-wrap gap-2" data-testid={testId}>
        {MOODS.map((mood) => (
          <button
            key={mood}
            type="button"
            aria-pressed={value === mood}
            className={`rounded-xl border px-3 py-2 text-sm ${
              value === mood
                ? "border-[var(--manovia-accent-text)] bg-[var(--manovia-accent-soft)] font-semibold"
                : "border-border-strong"
            }`}
            data-testid={`${testId}-${mood}`}
            onClick={() => onChange(value === mood ? null : mood)}
          >
            {t(`biofeedback.session.mood${mood}`)}
          </button>
        ))}
      </div>
    </fieldset>
  );
}

export function BiofeedbackView({ source, now }: BiofeedbackViewProps): ReactElement {
  const clock = useMemo(() => now ?? (() => Date.now()), [now]);
  const session = useBiofeedbackSession();
  const state = session.state;

  const sourceRef = useRef<HeartRateSource | null>(null);
  if (sourceRef.current === null) {
    sourceRef.current = source ?? webBluetoothSource();
  }
  const [supported, setSupported] = useState(() => sourceRef.current?.supported ?? false);
  const [status, setStatus] = useState<ConnectionStatus | null>(null);
  const [lastReading, setLastReading] = useState<HeartRateReading | null>(null);
  const [busy, setBusy] = useState(false);
  const [deviceError, setDeviceError] = useState<string | null>(null);
  const [dataOrigin, setDataOrigin] = useState<"wearable" | "csv" | null>(null);
  const [trend, setTrend] = useState<TrendPoint[]>([]);
  const [saved, setSaved] = useState(false);
  const connectionRef = useRef<{ disconnect(): Promise<void> } | null>(null);
  const csvSectionRef = useRef<HTMLDivElement | null>(null);

  const refreshTrend = useCallback(() => {
    void (async () => {
      try {
        const store = await openSummaryStore();
        const entries = await store.loadAll();
        setTrend(
          trendPoints(
            entries.map((entry) => entry.summary as Parameters<typeof trendPoints>[0][number]),
          ),
        );
      } catch {
        // Storage may be unavailable (private mode, locked-down browser);
        // the session itself must keep working regardless.
        setTrend([]);
      }
    })();
  }, []);

  useEffect(() => {
    refreshTrend();
  }, [refreshTrend]);

  // The session clock: one timer while a session runs, nothing otherwise.
  useEffect(() => {
    if (state.phase !== "running") {
      return;
    }
    const timer = setInterval(() => {
      session.tick(clock());
    }, TICK_MS);
    return () => clearInterval(timer);
    // `session` is a stable handle; re-run only on phase changes.
  }, [state.phase, session, clock]);

  const onConnect = useCallback(() => {
    setBusy(true);
    setDeviceError(null);
    void (async () => {
      try {
        const connection = await sourceRef.current!.connect();
        connectionRef.current = connection;
        connection.onStatus((next) => setStatus(next));
        connection.onReading((reading) => {
          setLastReading(reading);
          setDataOrigin("wearable");
          session.ingest([reading], clock());
        });
        setSupported(true);
      } catch (error) {
        if (error instanceof UserCancelledError) {
          setDeviceError(t("biofeedback.device.cancelled"));
        } else {
          setDeviceError(String(error));
        }
      } finally {
        setBusy(false);
      }
    })();
  }, [session, clock]);

  const onDisconnect = useCallback(() => {
    void connectionRef.current?.disconnect();
    connectionRef.current = null;
    setStatus(null);
  }, []);

  const onImported = useCallback(
    (samples: RRSample[]) => {
      session.importSamples(samples, clock());
      setDataOrigin("csv");
      setDeviceError(null);
    },
    [session, clock],
  );

  const qualityLevel = state.quality.level;

  return (
    <div className="space-y-6" data-testid="biofeedback-page">
      <PageHeader title={t("biofeedback.title")} lede={t("biofeedback.lede")} />

      <div className="space-y-2 rounded-2xl border border-border bg-surface p-5 text-sm">
        <p className="font-semibold" data-testid="wellness-badge">
          {t("biofeedback.wellnessBadge")}
        </p>
        <p className="text-ink-muted" data-testid="wellness-note">
          {t("biofeedback.wellnessNote")}
        </p>
        <p className="text-ink-muted" data-testid="privacy-note">
          {t("biofeedback.privacyNote")}
        </p>
      </div>

      <DevicePanel
        supported={supported}
        status={status}
        lastReading={lastReading}
        busy={busy}
        error={deviceError}
        onConnect={onConnect}
        onDisconnect={onDisconnect}
        onGoToCsv={() => csvSectionRef.current?.scrollIntoView({ block: "start" })}
      />

      <section
        className="space-y-4 rounded-2xl border border-border bg-surface p-6"
        aria-labelledby="biofeedback-session-heading"
        data-testid="session-panel"
      >
        <h2 id="biofeedback-session-heading" className="text-xl">
          {t("biofeedback.session.heading")}
        </h2>

        <PacerOrb state={state.pacer} />

        <div className="flex flex-wrap items-center gap-3 text-sm">
          <span className="rounded-xl border border-border px-3 py-1" data-testid="pacer-auto">
            {state.pacer.autoAdjust
              ? t("biofeedback.pacer.autoOn")
              : t("biofeedback.pacer.manualOn")}
          </span>
          {state.phase === "running" ? (
            <button
              type="button"
              className="rounded-xl border border-border-strong px-3 py-2 font-semibold"
              data-testid="pacer-toggle-auto"
              onClick={() => {
                if (state.pacer.autoAdjust) {
                  session.setManualRate(state.pacer.rateBpm);
                } else {
                  session.resumeAuto();
                }
              }}
            >
              {state.pacer.autoAdjust
                ? t("biofeedback.pacer.pauseAuto")
                : t("biofeedback.pacer.resumeAuto")}
            </button>
          ) : null}
        </div>

        <div className="space-y-2">
          <label className="font-semibold" htmlFor="biofeedback-rate">
            {t("biofeedback.pacer.manualRateLabel")}
          </label>
          <input
            id="biofeedback-rate"
            type="range"
            min={4.5}
            max={7}
            step={0.1}
            value={state.pacer.rateBpm}
            // 44px hit area (Day 25 target-size floor): a bare range input is
            // ~16px tall — too small for a thumb mid-session.
            className="h-11 w-full max-w-md"
            data-testid="pacer-rate-slider"
            onChange={(event) => session.setManualRate(Number(event.target.value))}
          />
          <p className="text-sm text-ink-muted">{t("biofeedback.pacer.boundsNote")}</p>
        </div>

        {state.phase === "idle" ? (
          <div className="space-y-4">
            <MoodPicker
              label={t("biofeedback.session.preMoodLabel")}
              value={state.preMood}
              onChange={(mood) => session.setPreMood(mood)}
              testId="pre-mood"
            />
            <button
              type="button"
              className="rounded-xl bg-[var(--manovia-accent)] px-4 py-2 font-semibold text-white"
              data-testid="session-start"
              onClick={() => session.start(state.preMood, clock(), dataOrigin ?? "wearable")}
            >
              {t("biofeedback.session.start")}
            </button>
          </div>
        ) : null}

        {state.phase === "running" ? (
          <div className="space-y-4">
            <button
              type="button"
              className="rounded-xl border-2 border-[var(--manovia-accent-text)] bg-[var(--manovia-accent-soft)] px-4 py-3 text-lg font-semibold"
              data-testid="dizzy-stop"
              onClick={() => session.dizzy(clock())}
            >
              {t("biofeedback.session.dizzy")}
            </button>
            <p className="text-sm text-ink-muted">{t("biofeedback.session.dizzyNote")}</p>
            <button
              type="button"
              className="rounded-xl border border-border-strong px-4 py-2 font-semibold"
              data-testid="session-end"
              onClick={() => session.end(state.postMood, "user", clock())}
            >
              {t("biofeedback.session.end")}
            </button>
          </div>
        ) : null}

        {state.phase === "ended" ? (
          <div className="space-y-4">
            <p className="font-semibold" data-testid="session-ended">
              {t("biofeedback.session.ended")}
            </p>
            <MoodPicker
              label={t("biofeedback.session.postMoodLabel")}
              value={state.postMood}
              onChange={(mood) => {
                // Re-end with the chosen mood: the summary carries it.
                session.end(mood, state.summary?.stopReason ?? "user", clock());
              }}
              testId="post-mood"
            />
            <button
              type="button"
              className="rounded-xl border border-border-strong px-4 py-2 font-semibold"
              data-testid="session-reset"
              onClick={() => {
                session.reset();
                setSaved(false);
              }}
            >
              {t("biofeedback.session.again")}
            </button>
          </div>
        ) : null}
      </section>

      <section
        className="space-y-4 rounded-2xl border border-border bg-surface p-6"
        aria-labelledby="biofeedback-metrics-heading"
        data-testid="metrics-panel"
      >
        <h2 id="biofeedback-metrics-heading" className="text-xl">
          {t("biofeedback.metrics.heading")}
        </h2>
        {qualityLevel === "poor" ? (
          <p data-testid="metrics-withheld" className="text-ink-muted">
            {t("biofeedback.metrics.withheld")}
          </p>
        ) : (
          <div className="space-y-2" data-testid="metrics-shown">
            <p>
              {t("biofeedback.metrics.rmssd")}:{" "}
              <span className="tabular-nums" data-testid="metric-rmssd">
                {state.current?.rmssdMs === null || state.current === null
                  ? "—"
                  : state.current.rmssdMs.toFixed(1)}
              </span>{" "}
              ms
            </p>
            <p>
              {t("biofeedback.metrics.meanHr")}:{" "}
              <span className="tabular-nums" data-testid="metric-hr">
                {state.current?.meanHrBpm === null || state.current === null
                  ? "—"
                  : state.current.meanHrBpm.toFixed(1)}
              </span>{" "}
              bpm
            </p>
          </div>
        )}
        <p data-testid="quality-level">
          {t("biofeedback.metrics.quality")}:{" "}
          {qualityLevel === "good"
            ? t("biofeedback.metrics.qualityGood")
            : qualityLevel === "fair"
              ? t("biofeedback.metrics.qualityFair")
              : t("biofeedback.metrics.qualityPoor")}
        </p>
        <p className="text-sm text-ink-muted" data-testid="beats-count">
          {t("biofeedback.metrics.beats", {
            accepted: state.counts.accepted,
            rejected: state.counts.rejected,
          })}
        </p>
      </section>

      <SummaryPanel
        summary={state.summary}
        trend={trend}
        saved={saved}
        onSave={() => {
          void (async () => {
            const store = await openSummaryStore();
            await store.save(state.summary);
            setSaved(true);
            refreshTrend();
          })();
        }}
        onDeleteAll={() => {
          void (async () => {
            const store = await openSummaryStore();
            await store.deleteAll();
            refreshTrend();
          })();
        }}
      />

      <div ref={csvSectionRef}>
        <CsvImportPanel onImported={onImported} />
      </div>
    </div>
  );
}
