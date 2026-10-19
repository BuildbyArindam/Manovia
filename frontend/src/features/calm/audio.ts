/**
 * Procedural calm audio (Day 46).
 *
 * Two detuned sine tones through a low-pass filter — a soft, static pad. There
 * is no sample, no download and no melody: the only thing that changes is the
 * pad's gain, which rises gently on the inhale and settles on the exhale. No
 * percussion, no loop point, nothing that could startle.
 *
 * Rules:
 * * Off unless asked for. The sensory profile's `sound: off` makes the control
 *   unavailable rather than silently starting it.
 * * Starts only from a user gesture (the browser requires it, and a calm space
 *   that makes noise on load is not calm).
 * * Gain changes use `setTargetAtTime`, never a step, so there is no click.
 * * `stop()` is idempotent and closes the context, because a sleeping tab that
 *   keeps an audio graph alive is a battery bug.
 */

export interface AudioParamLike {
  value: number;
  setTargetAtTime(value: number, startTime: number, timeConstant: number): void;
}

export interface AudioNodeLike {
  connect(destination: AudioNodeLike): AudioNodeLike;
  disconnect(): void;
}

export interface OscillatorLike extends AudioNodeLike {
  type: string;
  frequency: AudioParamLike;
  start(when?: number): void;
  stop(when?: number): void;
}

export interface GainLike extends AudioNodeLike {
  gain: AudioParamLike;
}

export interface FilterLike extends AudioNodeLike {
  type: string;
  frequency: AudioParamLike;
  Q: AudioParamLike;
}

export interface CalmAudioContext {
  readonly currentTime: number;
  readonly destination: AudioNodeLike;
  createOscillator(): OscillatorLike;
  createGain(): GainLike;
  createBiquadFilter(): FilterLike;
  close(): Promise<void>;
}

/** Master gain at the "low" setting. Audible in a quiet room, never loud. */
export const CALM_AUDIO_GAIN = 0.06;
/** The pad's resting gain; the breath moves it by at most ±40%. */
const PAD_BASE = 0.02;
const PAD_RANGE = 0.012;

export type AudioContextFactory = () => CalmAudioContext | null;

export function defaultAudioContextFactory(): AudioContextFactory {
  return () => {
    if (typeof window === "undefined") {
      return null;
    }
    const Ctor =
      (window as Window & { AudioContext?: new () => AudioContext }).AudioContext ??
      (window as Window & { webkitAudioContext?: new () => AudioContext }).webkitAudioContext;
    if (Ctor === undefined) {
      return null;
    }
    try {
      return new Ctor() as unknown as CalmAudioContext;
    } catch {
      return null;
    }
  };
}

export class CalmAudio {
  private context: CalmAudioContext | null = null;
  private gain: GainLike | null = null;
  private oscillators: OscillatorLike[] = [];
  private stopped = false;

  constructor(private readonly factory: AudioContextFactory = defaultAudioContextFactory()) {}

  get running(): boolean {
    return this.context !== null && !this.stopped;
  }

  /**
   * Start the pad. Returns false where Web Audio is unavailable or the browser
   * refused to create a context — a silent calm space, not an error screen.
   */
  start(): boolean {
    if (this.context !== null && !this.stopped) {
      return true;
    }
    const context = this.factory();
    if (context === null) {
      return false;
    }
    this.context = context;
    this.stopped = false;

    const master = context.createGain();
    master.gain.value = CALM_AUDIO_GAIN;
    master.connect(context.destination);
    this.gain = master;

    const filter = context.createBiquadFilter();
    filter.type = "lowpass";
    filter.frequency.value = 520;
    filter.Q.value = 0.4;
    filter.connect(master);

    // A root and its fifth, detuned by a few cents: slow beating, no melody.
    for (const [frequency, detune] of [
      [110, -3],
      [164.81, 2],
      [220, -2],
    ] as const) {
      const oscillator = context.createOscillator();
      oscillator.type = "sine";
      oscillator.frequency.value = frequency + detune / 100;
      oscillator.connect(filter);
      oscillator.start();
      this.oscillators.push(oscillator);
    }

    this.setBreathScale(0.55);
    return true;
  }

  /** Follow the breathing curve. Safe before `start()` and after `stop()`. */
  setBreathScale(scale: number): void {
    if (this.gain === null || this.context === null || this.stopped) {
      return;
    }
    const t = Number.isFinite(scale) ? Math.min(1, Math.max(0, (scale - 0.55) / 0.45)) : 0;
    this.gain.gain.setTargetAtTime(PAD_BASE + PAD_RANGE * t, this.context.currentTime, 0.35);
  }

  stop(): void {
    if (this.stopped) {
      return;
    }
    this.stopped = true;
    for (const oscillator of this.oscillators) {
      try {
        oscillator.stop();
        oscillator.disconnect();
      } catch {
        // Already stopped by the context; nothing to undo.
      }
    }
    this.oscillators = [];
    this.gain = null;
    const context = this.context;
    this.context = null;
    if (context !== null) {
      void context.close().catch(() => undefined);
    }
  }
}
