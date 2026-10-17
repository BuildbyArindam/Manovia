import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ReactElement } from "react";

import { api, ApiError, type ApiClient } from "@/lib/api";
import { i18n, t } from "@/lib/i18n/i18n";
import { usePrefersReducedMotion } from "@/lib/usePrefersReducedMotion";

import { activeCaption, MeditationAudioPlayer } from "./audioPlayer";
import {
  createMeditationScript,
  fetchMeditationCaptions,
  fetchThemes,
  quickRequestForNow,
  renderMeditationAudio,
} from "./endpoints";
import { SoundscapeEngine } from "./soundscape/engine";
import { SOUNDSCAPE_PALETTES, paletteById, type EnergyLevel } from "./soundscape/palettes";
import { readLibrary, saveFavourite, writeLibrary } from "./library";
import type { LibraryState } from "./library";
import type {
  Caption,
  MeditationContext,
  MeditationLanguage,
  MeditationRequestPayload,
  MeditationScript,
  MeditationThemeId,
  PreferenceTag,
  ThemesResponse,
} from "./types";
import { MAX_DURATION_SECONDS, MIN_DURATION_SECONDS, PREFERENCE_TAGS } from "./types";

export interface MeditationViewProps {
  client?: ApiClient;
}

type PlayState = "idle" | "rendering" | "playing";

const SPEEDS = [
  { value: 0.85, labelKey: "meditation.form.speed.slower" },
  { value: 1.0, labelKey: "meditation.form.speed.normal" },
  { value: 1.15, labelKey: "meditation.form.speed.faster" },
] as const;

function currentLanguage(): MeditationLanguage {
  const lang = i18n.language ?? "en";
  if (lang.startsWith("hi")) {
    return "hi";
  }
  if (lang.startsWith("bn")) {
    return "bn";
  }
  return "en";
}

function paletteLabelKey(id: string): string {
  try {
    return paletteById(id).labelKey;
  } catch {
    return "meditation.soundscape.unknownPalette";
  }
}

function minutesOptions(): Array<{ value: number; minutes: number }> {
  const options: Array<{ value: number; minutes: number }> = [];
  const minMin = Math.ceil(MIN_DURATION_SECONDS / 60);
  const maxMin = Math.floor(MAX_DURATION_SECONDS / 60);
  for (let m = minMin; m <= maxMin; m += 1) {
    options.push({ value: m * 60, minutes: m });
  }
  return options;
}

export function MeditationView({ client = api }: MeditationViewProps): ReactElement {
  const prefersReduced = usePrefersReducedMotion();
  const language = currentLanguage();
  const durationOptions = useMemo(minutesOptions, []);

  // --- composition form -------------------------------------------------
  const [themes, setThemes] = useState<ThemesResponse | null>(null);
  const [theme, setTheme] = useState<MeditationThemeId>("auto");
  const [duration, setDuration] = useState(180);
  const [speed, setSpeed] = useState(1.0);
  const [context, setContext] = useState<MeditationContext>("seated");
  const [eyesOpen, setEyesOpen] = useState(false);
  const [prefs, setPrefs] = useState<PreferenceTag[]>([]);

  // --- the session ------------------------------------------------------
  const [script, setScript] = useState<MeditationScript | null>(null);
  const [captions, setCaptions] = useState<Caption[]>([]);
  const [composing, setComposing] = useState(false);
  const [playState, setPlayState] = useState<PlayState>("idle");
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState<string | null>(null);

  // --- soundscape ---------------------------------------------------------
  const [paletteId, setPaletteId] = useState("soft-rain");
  const [volume, setVolume] = useState(50); // percent
  const [reducedStim, setReducedStim] = useState(prefersReduced);
  const [adapt, setAdapt] = useState(false);
  const [pace, setPace] = useState(6); // breaths per minute
  const [energy, setEnergy] = useState<EnergyLevel>("normal");

  // --- library ------------------------------------------------------------
  const [library, setLibrary] = useState<LibraryState>(() => readLibrary());
  const [label, setLabel] = useState("");
  const [savedFlash, setSavedFlash] = useState(false);

  const engineRef = useRef<SoundscapeEngine | null>(null);
  const playerRef = useRef<MeditationAudioPlayer | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  // Default the reduced-stimulation toggle on when the OS asks for it.
  useEffect(() => {
    if (prefersReduced) {
      setReducedStim(true);
    }
  }, [prefersReduced]);

  useEffect(() => {
    void fetchThemes(client).then(setThemes).catch(() => setThemes(null));
  }, [client]);

  // Tear everything down on unmount: voice, soundscape, in-flight renders.
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
      playerRef.current?.stop();
      void engineRef.current?.dispose();
    };
  }, []);

  const ensurePlayer = useCallback((): MeditationAudioPlayer => {
    if (playerRef.current === null) {
      const player = new MeditationAudioPlayer();
      player.onTime(setElapsed);
      player.onEnded(() => {
        setPlayState("idle");
        setElapsed(0);
        engineRef.current?.stop();
      });
      playerRef.current = player;
    }
    return playerRef.current;
  }, []);

  const ensureEngine = useCallback((): SoundscapeEngine | null => {
    if (engineRef.current === null) {
      engineRef.current = new SoundscapeEngine();
      engineRef.current.setPace(pace);
      engineRef.current.setReducedStimulation(reducedStim);
      engineRef.current.setAdaptation(adapt);
      engineRef.current.setEnergy(energy);
    }
    return engineRef.current;
  }, [adapt, energy, pace, reducedStim]);

  const applyPalette = useCallback(
    (id: string) => {
      engineRef.current?.setPalette(paletteById(id));
    },
    [],
  );

  const requestScript = useCallback(
    async (payload: MeditationRequestPayload): Promise<MeditationScript | null> => {
      setComposing(true);
      setError(null);
      try {
        const next = await createMeditationScript(payload, client);
        setScript(next);
        setPlayState("idle");
        setElapsed(0);
        playerRef.current?.stop();
        // Record the composition in the local "play again" history.
        setLibrary((lib) => {
          const history = [
            next.content_hash,
            ...lib.history.filter((h) => h !== next.content_hash),
          ].slice(0, 10);
          const nextLib = { ...lib, history };
          writeLibrary(nextLib);
          return nextLib;
        });
        // Captions come with the render; fetch them now so the live line is
        // ready the moment audio starts.
        const cap = await fetchMeditationCaptions(next, client).catch(() => null);
        setCaptions(cap?.captions ?? []);
        return next;
      } catch (err) {
        setError(err instanceof ApiError ? err.message : t("meditation.errors.unknown"));
        return null;
      } finally {
        setComposing(false);
      }
    },
    [client],
  );

  const play = useCallback(
    async (withSoundscape: boolean) => {
      if (script === null || composing || playState === "rendering") {
        return;
      }
      setError(null);
      setPlayState("rendering");
      const controller = new AbortController();
      abortRef.current = controller;
      try {
        if (withSoundscape) {
          const engine = ensureEngine();
          if (engine !== null) {
            engine.setVolume(volume / 100);
            applyPalette(paletteId);
            engine.setPace(pace);
            engine.setReducedStimulation(reducedStim);
            engine.setAdaptation(adapt);
            engine.setEnergy(energy);
            engine.start();
          }
        }
        const response = await renderMeditationAudio(script, client, controller.signal);
        if (!response.ok) {
          throw new ApiError(
            response.status,
            "meditation_audio",
            `audio render failed (${response.status})`,
            "",
          );
        }
        const bytes = await response.arrayBuffer();
        const player = ensurePlayer();
        player.setVolume(0.9);
        player.load(bytes);
        await player.play();
        setPlayState("playing");
      } catch (err) {
        if (err instanceof DOMException && err.name === "AbortError") {
          return; // unmounted mid-render
        }
        setError(err instanceof Error ? err.message : t("meditation.errors.unknown"));
        setPlayState("idle");
      }
    },
    [
      adapt,
      applyPalette,
      client,
      composing,
      ensureEngine,
      ensurePlayer,
      energy,
      paletteId,
      pace,
      playState,
      reducedStim,
      script,
      volume,
    ],
  );

  /** The "stop instantly" control: voice and soundscape stop now. */
  const stopNow = useCallback(() => {
    playerRef.current?.stop();
    engineRef.current?.stop();
    abortRef.current?.abort();
    setPlayState("idle");
    setElapsed(0);
  }, []);

  const save = useCallback(() => {
    if (script === null) {
      return;
    }
    const name = label.trim() === "" ? t("meditation.library.defaultLabel") : label.trim();
    setLibrary((lib) => {
      const next = saveFavourite(lib, script, name, paletteId);
      writeLibrary(next);
      return next;
    });
    setLabel("");
    setSavedFlash(true);
    window.setTimeout(() => setSavedFlash(false), 2000);
  }, [label, paletteId, script]);

  const themeLabel = useCallback((id: MeditationThemeId): string => {
    if (id !== "auto") {
      const found = themes?.themes.find((th) => th.id === id);
      if (found !== undefined) {
        return found.label;
      }
    }
    return t(`meditation.form.theme.${id}`);
  }, [themes]);

  const liveCaption = activeCaption(captions, elapsed);

  return (
    <div className="space-y-6" data-testid="meditation-page">
      <header className="space-y-1">
        <h1 className="text-2xl font-semibold">{t("meditation.title")}</h1>
        <p className="text-sm text-ink-muted">{t("meditation.lede")}</p>
      </header>

      <div className="grid gap-4 lg:grid-cols-2">
        {/* ---------------- composition ---------------- */}
        <section className="space-y-4 rounded-2xl border border-border bg-surface p-5" aria-labelledby="med-composition">
          <h2 id="med-composition" className="text-xl">
            {t("meditation.form.title")}
          </h2>

          <button
            type="button"
            className="btn-primary w-full"
            onClick={() => {
              void requestScript(quickRequestForNow(context, language));
            }}
            disabled={composing}
          >
            {t("meditation.quick.title")}
          </button>

          <hr className="border-border" />

          <label className="flex flex-col gap-1 text-sm">
            <span>{t("meditation.form.themeLabel")}</span>
            <select
              className="min-h-11 rounded-lg border border-border-strong bg-surface px-3 py-2"
              value={theme}
              onChange={(e) => setTheme(e.target.value as MeditationThemeId)}
            >
              <option value="auto">{t("meditation.form.theme.auto")}</option>
              {themes?.themes.map((th) => (
                <option key={th.id} value={th.id}>
                  {th.label}
                </option>
              ))}
            </select>
          </label>

          <label className="flex flex-col gap-1 text-sm">
            <span>{t("meditation.form.durationLabel")}</span>
            <select
              className="min-h-11 rounded-lg border border-border-strong bg-surface px-3 py-2"
              value={duration}
              onChange={(e) => setDuration(Number(e.target.value))}
            >
              {durationOptions.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {t("meditation.form.duration.value", { minutes: opt.minutes })}
                </option>
              ))}
            </select>
          </label>

          <label className="flex flex-col gap-1 text-sm">
            <span>{t("meditation.form.speedLabel")}</span>
            <select
              className="min-h-11 rounded-lg border border-border-strong bg-surface px-3 py-2"
              value={speed}
              onChange={(e) => setSpeed(Number(e.target.value))}
            >
              {SPEEDS.map((s) => (
                <option key={s.value} value={s.value}>
                  {t(s.labelKey)}
                </option>
              ))}
            </select>
          </label>

          <label className="flex flex-col gap-1 text-sm">
            <span>{t("meditation.form.contextLabel")}</span>
            <select
              className="min-h-11 rounded-lg border border-border-strong bg-surface px-3 py-2"
              value={context}
              onChange={(e) => {
                const next = e.target.value as MeditationContext;
                setContext(next);
                // Closed eyes are only offered when the person is still.
                if (next !== "seated") {
                  setEyesOpen(false);
                }
              }}
            >
              <option value="seated">{t("meditation.form.context.seated")}</option>
              <option value="walking">{t("meditation.form.context.walking")}</option>
              <option value="driving">{t("meditation.form.context.driving")}</option>
              <option value="unknown">{t("meditation.form.context.unknown")}</option>
            </select>
          </label>

          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              className="size-4"
              checked={eyesOpen}
              disabled={context !== "seated"}
              onChange={(e) => setEyesOpen(e.target.checked)}
            />
            <span>{t("meditation.form.eyesOpen")}</span>
          </label>

          <fieldset className="space-y-1 text-sm">
            <legend>{t("meditation.form.preferences")}</legend>
            <div className="flex flex-wrap gap-2">
              {PREFERENCE_TAGS.map((tag) => {
                const on = prefs.includes(tag);
                return (
                  <button
                    key={tag}
                    type="button"
                    aria-pressed={on}
                    className="rounded-full border border-border-strong px-3 py-1.5 text-xs"
                    onClick={() =>
                      setPrefs((prev) =>
                        on ? prev.filter((p) => p !== tag) : prev.length < 4 ? [...prev, tag] : prev,
                      )
                    }
                  >
                    {t(`meditation.form.pref.${tag}`)}
                  </button>
                );
              })}
            </div>
          </fieldset>

          <button
            type="button"
            className="btn-primary w-full"
            disabled={composing}
            onClick={() => {
              void requestScript({
                theme,
                duration_seconds: duration,
                voice_speed: speed,
                language,
                hour_of_day: new Date().getHours(),
                context,
                eyes_open: eyesOpen && context === "seated",
                preferences: prefs,
              });
            }}
          >
            {composing ? t("meditation.form.composing") : t("meditation.form.compose")}
          </button>
        </section>

        {/* ---------------- the session ---------------- */}
        <section className="space-y-4 rounded-2xl border border-border bg-surface p-5" aria-labelledby="med-session">
          <h2 id="med-session" className="text-xl">
            {t("meditation.session.title")}
          </h2>

          {error !== null && (
            <p role="alert" className="rounded-lg bg-danger/10 p-3 text-sm text-danger">
              {error}
            </p>
          )}

          {script === null ? (
            <p className="text-sm text-ink-muted">{t("meditation.session.empty")}</p>
          ) : (
            <>
              <div className="space-y-2">
                <p className="font-semibold">
                  {themeLabel(script.theme)}
                  {" · "}
                  {t("meditation.session.estimated", { seconds: script.estimated_duration_s })}
                  {" · "}
                  {t("meditation.session.words", { count: script.word_count })}
                </p>
                <ul className="flex flex-wrap gap-2 text-xs" aria-label={t("meditation.session.safetyLabel")}>
                  {script.safety.stop_cue && (
                    <li className="rounded-full bg-surface-muted px-3 py-1">{t("meditation.session.safety.stop")}</li>
                  )}
                  {script.safety.safety_preface && (
                    <li className="rounded-full bg-surface-muted px-3 py-1">{t("meditation.session.safety.preface")}</li>
                  )}
                  {script.safety.eyes_open && (
                    <li className="rounded-full bg-surface-muted px-3 py-1">{t("meditation.session.safety.eyesOpen")}</li>
                  )}
                  {script.safety.driving_warning && (
                    <li className="rounded-full bg-surface-muted px-3 py-1">{t("meditation.session.safety.driving")}</li>
                  )}
                </ul>
              </div>

              <div className="space-y-2" data-testid="meditation-transcript">
                {script.sections.map((section) => (
                  <p
                    key={section.id}
                    className="text-sm leading-relaxed"
                    data-testid={`section-${section.role}`}
                  >
                    {section.text}
                  </p>
                ))}
              </div>

              <div className="space-y-3" data-testid="meditation-controls">
                {playState === "playing" ? (
                  <div className="flex flex-wrap items-center gap-3">
                    <button type="button" className="btn-secondary" onClick={stopNow} data-testid="stop-now">
                      {t("meditation.player.stop")}
                    </button>
                    <span className="text-sm tabular-nums text-ink-muted">
                      {Math.floor(elapsed / 60)}:{String(Math.floor(elapsed % 60)).padStart(2, "0")}
                    </span>
                  </div>
                ) : (
                  <div className="flex flex-wrap gap-3">
                    <button
                      type="button"
                      className="btn-primary"
                      onClick={() => {
                        void play(true);
                      }}
                      disabled={playState === "rendering" || composing}
                      data-testid="play-soundscape"
                    >
                      {playState === "rendering"
                        ? t("meditation.player.rendering")
                        : t("meditation.player.playSoundscape")}
                    </button>
                    <button
                      type="button"
                      className="btn-secondary"
                      onClick={() => {
                        void play(false);
                      }}
                      disabled={playState === "rendering" || composing}
                      data-testid="play-voice"
                    >
                      {t("meditation.player.playVoiceOnly")}
                    </button>
                  </div>
                )}
                <p className="min-h-5 text-sm" aria-live="polite" data-testid="live-caption">
                  {liveCaption?.text ?? ""}
                </p>
              </div>

              {/* ---------------- soundscape ---------------- */}
              <details className="rounded-xl border border-border p-3" open={playState === "playing"}>
                <summary className="cursor-pointer text-sm font-semibold">
                  {t("meditation.soundscape.title")}
                </summary>
                <div className="mt-3 space-y-3 text-sm">
                  <fieldset className="space-y-1">
                    <legend className="text-ink-muted">{t("meditation.soundscape.palette")}</legend>
                    <div className="flex flex-wrap gap-2">
                      {SOUNDSCAPE_PALETTES.map((palette) => (
                        <button
                          key={palette.id}
                          type="button"
                          aria-pressed={paletteId === palette.id}
                          className="rounded-full border border-border-strong px-3 py-1.5 text-xs"
                          onClick={() => {
                            setPaletteId(palette.id);
                            applyPalette(palette.id);
                          }}
                        >
                          {t(palette.labelKey)}
                        </button>
                      ))}
                    </div>
                  </fieldset>

                  <label className="flex items-center gap-3">
                    <span>{t("meditation.soundscape.volume")}</span>
                    <input
                      type="range"
                      min={0}
                      max={100}
                      value={volume}
                      className="min-w-28 flex-1"
                      onChange={(e) => {
                        const next = Number(e.target.value);
                        setVolume(next);
                        engineRef.current?.setVolume(next / 100);
                      }}
                    />
                    <span className="tabular-nums text-ink-muted">{volume}%</span>
                  </label>

                  <label className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      className="size-4"
                      checked={reducedStim}
                      onChange={(e) => {
                        setReducedStim(e.target.checked);
                        engineRef.current?.setReducedStimulation(e.target.checked);
                      }}
                    />
                    <span>{t("meditation.soundscape.reduced")}</span>
                  </label>

                  <label className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      className="size-4"
                      checked={adapt}
                      onChange={(e) => {
                        setAdapt(e.target.checked);
                        engineRef.current?.setAdaptation(e.target.checked);
                      }}
                    />
                    <span>{t("meditation.soundscape.adapt")}</span>
                  </label>

                  {adapt && (
                    <div className="space-y-2">
                      <label className="flex items-center gap-3">
                        <span>{t("meditation.soundscape.pace")}</span>
                        <input
                          type="range"
                          min={4}
                          max={12}
                          step={0.5}
                          value={pace}
                          className="min-w-24 flex-1"
                          onChange={(e) => {
                            const next = Number(e.target.value);
                            setPace(next);
                            engineRef.current?.setPace(next);
                          }}
                        />
                        <span className="tabular-nums text-ink-muted">{pace}</span>
                      </label>
                      <label className="flex flex-col gap-1">
                        <span>{t("meditation.soundscape.energyLabel")}</span>
                        <select
                          className="min-h-10 rounded-lg border border-border-strong bg-surface px-3 py-1.5"
                          value={energy}
                          onChange={(e) => {
                            const next = e.target.value as EnergyLevel;
                            setEnergy(next);
                            engineRef.current?.setEnergy(next);
                          }}
                        >
                          <option value="low">{t("meditation.soundscape.energy.low")}</option>
                          <option value="normal">{t("meditation.soundscape.energy.normal")}</option>
                          <option value="high">{t("meditation.soundscape.energy.high")}</option>
                        </select>
                      </label>
                    </div>
                  )}
                </div>
              </details>

              {/* ---------------- save to library ---------------- */}
              <div className="flex flex-wrap items-end gap-2 border-t border-border pt-3">
                <label className="flex min-w-40 flex-1 flex-col gap-1 text-sm">
                  <span>{t("meditation.library.label")}</span>
                  <input
                    type="text"
                    className="min-h-10 rounded-lg border border-border-strong bg-surface px-3 py-1.5"
                    value={label}
                    placeholder={t("meditation.library.labelPlaceholder")}
                    onChange={(e) => setLabel(e.target.value)}
                  />
                </label>
                <button type="button" className="btn-secondary" onClick={save}>
                  {t("meditation.library.save")}
                </button>
                {savedFlash && (
                  <span role="status" className="text-sm text-accent">
                    {t("meditation.library.saved")}
                  </span>
                )}
              </div>
            </>
          )}
        </section>
      </div>

      {/* ---------------- library ---------------- */}
      <section className="rounded-2xl border border-border bg-surface p-5" aria-labelledby="med-library">
        <h2 id="med-library" className="text-xl">
          {t("meditation.library.title")}
        </h2>
        {library.entries.length === 0 ? (
          <p className="mt-2 text-sm text-ink-muted">{t("meditation.library.empty")}</p>
        ) : (
          <ul className="mt-3 divide-y divide-border">
            {library.entries.map((entry) => (
              <li key={entry.script.content_hash} className="flex flex-wrap items-center gap-3 py-3">
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-semibold">{entry.label}</p>
                  <p className="text-xs text-ink-muted">
                    {t("meditation.library.entryMeta", {
                      theme: themeLabel(entry.script.theme),
                      minutes: Math.round(entry.script.estimated_duration_s / 60),
                      palette: t(paletteLabelKey(entry.palette_id)),
                    })}
                  </p>
                </div>
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() => {
                    setScript(entry.script);
                    setPlayState("idle");
                    setElapsed(0);
                  }}
                >
                  {t("meditation.library.load")}
                </button>
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() =>
                    setLibrary((lib) => {
                      const next = {
                        ...lib,
                        entries: lib.entries.filter(
                          (e) => e.script.content_hash !== entry.script.content_hash,
                        ),
                      };
                      writeLibrary(next);
                      return next;
                    })
                  }
                >
                  {t("meditation.library.remove")}
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
