/** Day 45: the adaptive soundscape engine.
 *
 * Real-time procedural audio over the Web Audio API: a low pad chord
 * (sine oscillators through a lowpass filter), a gentle filtered-noise
 * texture, and LFO motion coupled to a breathing pacer. Everything is
 * generated in the browser — no samples, no network — so it works offline
 * once the page is loaded.
 *
 * Safety rails, all testable:
 *
 * * **Hard volume limit.** The master gain is clamped to
 *   {@link MAX_MASTER_GAIN} no matter what the volume slider says, and a
 *   high-ratio compressor sits in front of the destination as a soft
 *   ceiling. The UI cannot exceed the rail; the rail is in the engine.
 * * **Slow and bounded adaptation.** Pace and brightness move toward their
 *   targets with a small lerp each tick and are clamped to the palette's
 *   ranges — a change of mood is a drift, never a jump.
 * * **Fade in/out** on start and stop (linear gain ramps, {@link FADE_SECONDS}).
 * * **Reduced-stimulation mode** cuts the texture, halves the LFO depth and
 *   slows adaptation further; it defaults on when the OS asks for reduced
 *   motion.
 *
 * The context factory and clock are injected, so the node graph and the
 * adaptation arithmetic are unit-testable with a mocked AudioContext (the
 * same convention as `features/chat/audioPlayback.ts`).
 */

import type { EnergyLevel, SoundscapePalette } from "./palettes";
import { brightnessForEnergy, clampPace, MAX_PACE_BPM, MIN_PACE_BPM, paletteById } from "./palettes";

const DEFAULT_PALETTE = paletteById("soft-rain");

/** The loudest the engine will ever get, linear gain (≈ -14 dBFS). */
export const MAX_MASTER_GAIN = 0.2;
/** Fade-in and fade-out length, seconds. */
export const FADE_SECONDS = 1.5;
/** Per-tick fraction of the remaining gap (pace/brightness lerp). */
export const ADAPT_LERP = 0.06;
/** Tick interval for the adaptation loop, seconds. */
export const TICK_SECONDS = 0.5;
/** The limiter's soft ceiling. */
export const LIMITER = { thresholdDb: -12, kneeDb: 6, ratio: 12, attack: 0.01, release: 0.3 } as const;

export interface SoundscapeNode {
  id: string;
  type: string;
  params: Record<string, string | number>;
}

export interface GraphDescription {
  engine: string;
  running: boolean;
  paletteId: string;
  reducedStimulation: boolean;
  adaptationEnabled: boolean;
  master: { ceiling: number; currentGain: number; volume: number };
  limiter: typeof LIMITER;
  nodes: SoundscapeNode[];
  edges: Array<[string, string]>;
  pace: { actualBpm: number; targetBpm: number; minBpm: number; maxBpm: number };
  brightness: { actualHz: number; targetHz: number; minHz: number; maxHz: number };
}

export interface AudioParamLike {
  value: number;
  setValueAtTime(value: number, time: number): unknown;
  linearRampToValueAtTime(value: number, time: number): unknown;
}

export interface OscillatorNodeLike {
  type: string;
  frequency: AudioParamLike;
  connect(destination: AudioNodeLike): AudioNodeLike;
  start(when?: number): void;
  stop(when?: number): void;
}

export interface BufferSourceNodeLike {
  loop: boolean;
  buffer: unknown;
  connect(destination: AudioNodeLike): AudioNodeLike;
  start(when?: number): void;
  stop(when?: number): void;
}

export interface FilterNodeLike {
  type: string;
  frequency: AudioParamLike;
  Q: AudioParamLike;
  connect(destination: AudioNodeLike): AudioNodeLike;
}

export interface GainNodeLike {
  gain: AudioParamLike;
  connect(destination: AudioNodeLike): AudioNodeLike;
}

export interface CompressorNodeLike {
  threshold: AudioParamLike;
  knee: AudioParamLike;
  ratio: AudioParamLike;
  attack: AudioParamLike;
  release: AudioParamLike;
  connect(destination: AudioNodeLike): AudioNodeLike;
}

export interface AudioNodeLike {
  connect(destination: AudioNodeLike): AudioNodeLike;
}

export interface AudioBufferLike {
  getChannelData(channel: number): Float32Array;
}

export interface SoundscapeContext {
  currentTime: number;
  destination: AudioNodeLike;
  state: string;
  resume(): Promise<void>;
  close(): Promise<void>;
  createOscillator(): OscillatorNodeLike;
  createGain(): GainNodeLike;
  createBiquadFilter(): FilterNodeLike;
  createDynamicsCompressor(): CompressorNodeLike;
  createBufferSource(): BufferSourceNodeLike;
  createBuffer(channels: number, length: number, sampleRate: number): AudioBufferLike;
}

export type AudioContextFactory = () => SoundscapeContext;

/** The engine itself. One instance per page. */
export class SoundscapeEngine {
  private context: SoundscapeContext | null = null;
  private readonly oscillators: OscillatorNodeLike[] = [];
  private noiseSource: BufferSourceNodeLike | null = null;
  private breathLfo: OscillatorNodeLike | null = null;
  private swellLfo: OscillatorNodeLike | null = null;
  private padFilter: FilterNodeLike | null = null;
  private noiseGain: GainNodeLike | null = null;
  private lfoAmp: GainNodeLike | null = null;
  private masterGain: GainNodeLike | null = null;

  private readonly edges: Array<[string, string]> = [];
  private readonly nodeRegistry = new Map<string, SoundscapeNode>();

  private palette: SoundscapePalette;
  private volume = 0.5;
  private reducedStimulation = false;
  private adaptationEnabled = false;
  private energy: EnergyLevel = "normal";
  private actualPace: number;
  private targetPace: number;
  private actualCutoff: number;
  private targetCutoff: number;
  private running = false;
  private tickTimer: ReturnType<typeof setInterval> | null = null;

  constructor(
    private readonly createContext: AudioContextFactory = browserContext,
    palette: SoundscapePalette = DEFAULT_PALETTE,
  ) {
    this.palette = palette;
    this.actualPace = palette.defaultPaceBpm;
    this.targetPace = palette.defaultPaceBpm;
    const mid = (palette.cutoffRange[0] + palette.cutoffRange[1]) / 2;
    this.actualCutoff = mid;
    this.targetCutoff = mid;
  }

  get isRunning(): boolean {
    return this.running;
  }

  /** Build the graph and fade in. Idempotent: starting twice is a no-op. */
  start(): void {
    if (this.running) {
      return;
    }
    const context = this.createContext();
    this.context = context;
    this.edges.length = 0;
    this.nodeRegistry.clear();

    const master = context.createGain();
    this.register("master", "GainNode", master, { ceiling: MAX_MASTER_GAIN });
    const limiter = context.createDynamicsCompressor();
    this.register("limiter", "DynamicsCompressorNode", limiter, {
      threshold: LIMITER.thresholdDb,
      ratio: LIMITER.ratio,
      knee: LIMITER.kneeDb,
    });
    limiter.threshold.value = LIMITER.thresholdDb;
    limiter.knee.value = LIMITER.kneeDb;
    limiter.ratio.value = LIMITER.ratio;
    limiter.attack.value = LIMITER.attack;
    limiter.release.value = LIMITER.release;

    const padGain = context.createGain();
    this.register("pad-gain", "GainNode", padGain, { level: 0.5 });
    const padFilter = context.createBiquadFilter();
    padFilter.type = "lowpass";
    padFilter.frequency.value = this.actualCutoff;
    padFilter.Q.value = 0.7;
    this.register("pad-filter", "BiquadFilterNode", padFilter, {
      type: "lowpass",
      frequency: this.actualCutoff,
    });

    // The pad chord: one sine per note, plus a sub an octave below the
    // lowest note for warmth. A hair of detune keeps it from sounding like
    // a test tone.
    const detunes = [0, 3, -3, 2, -2];
    this.palette.chord.forEach((frequency, i) => {
      const osc = context.createOscillator();
      osc.type = "sine";
      const detune = detunes[i % detunes.length] ?? 0;
      osc.frequency.value = frequency * (1 + detune / 1000);
      this.register(`osc-${i + 1}`, "OscillatorNode", osc, {
        type: "sine",
        frequency: osc.frequency.value,
      });
      this.connect(osc, padGain, `osc-${i + 1}`, "pad-gain");
      this.oscillators.push(osc);
    });
    const sub = context.createOscillator();
    sub.type = "sine";
    sub.frequency.value = (this.palette.chord[0] ?? 220) / 2;
    this.register("osc-sub", "OscillatorNode", sub, {
      type: "sine",
      frequency: sub.frequency.value,
    });
    this.connect(sub, padGain, "osc-sub", "pad-gain");
    this.oscillators.push(sub);

    // The texture: a looping white-noise buffer through a bandpass.
    const buffer = context.createBuffer(1, 2 * 22050, 22050);
    const data = buffer.getChannelData(0);
    for (let i = 0; i < data.length; i += 1) {
      data[i] = Math.random() * 2 - 1;
    }
    const noiseSource = context.createBufferSource();
    noiseSource.loop = true;
    noiseSource.buffer = buffer;
    this.register("noise-source", "AudioBufferSourceNode", noiseSource, { loop: 1 });
    const noiseFilter = context.createBiquadFilter();
    noiseFilter.type = "bandpass";
    noiseFilter.frequency.value = this.palette.textureCentre;
    noiseFilter.Q.value = 0.7;
    this.register("noise-filter", "BiquadFilterNode", noiseFilter, {
      type: "bandpass",
      frequency: this.palette.textureCentre,
    });
    const noiseGain = context.createGain();
    this.register("noise-gain", "GainNode", noiseGain, { level: this.noiseLevel() });

    // The breathing motion: an LFO at the pace rate drives the pad
    // filter's brightness, a slower LFO swells the pad gain.
    const lfoAmp = context.createGain();
    this.register("lfo-amp", "GainNode", lfoAmp, { depth: this.lfoDepth() });
    const breathLfo = context.createOscillator();
    breathLfo.type = "sine";
    breathLfo.frequency.value = this.actualPace / 60;
    this.register("breath-lfo", "OscillatorNode", breathLfo, {
      type: "sine",
      frequency: breathLfo.frequency.value,
    });
    const swellAmp = context.createGain();
    swellAmp.gain.value = 0.12 * (this.reducedStimulation ? 0.5 : 1);
    this.register("swell-amp", "GainNode", swellAmp, { depth: swellAmp.gain.value });
    const swellLfo = context.createOscillator();
    swellLfo.type = "sine";
    swellLfo.frequency.value = this.actualPace / 120;
    this.register("swell-lfo", "OscillatorNode", swellLfo, {
      type: "sine",
      frequency: swellLfo.frequency.value,
    });

    this.padFilter = padFilter;
    this.noiseGain = noiseGain;
    this.lfoAmp = lfoAmp;
    this.masterGain = master;
    this.noiseSource = noiseSource;
    this.breathLfo = breathLfo;
    this.swellLfo = swellLfo;

    this.connect(padFilter, padGain, "pad-filter", "pad-gain");
    this.connect(noiseFilter, noiseGain, "noise-filter", "noise-gain");
    this.connect(padGain, master, "pad-gain", "master");
    this.connect(noiseGain, master, "noise-gain", "master");
    this.connect(noiseSource, noiseFilter, "noise-source", "noise-filter");
    this.connect(breathLfo, lfoAmp, "breath-lfo", "lfo-amp");
    this.connect(lfoAmp, padFilter, "lfo-amp", "pad-filter");
    this.connect(swellLfo, swellAmp, "swell-lfo", "swell-amp");
    this.connect(swellAmp, padGain, "swell-amp", "pad-gain");
    this.connect(master, limiter, "master", "limiter");
    this.connect(limiter, context.destination, "limiter", "destination");

    const now = context.currentTime;
    master.gain.value = 0;
    master.gain.linearRampToValueAtTime(this.masterCeiling(), now + FADE_SECONDS);

    this.oscillators.forEach((osc) => osc.start(now));
    breathLfo.start(now);
    swellLfo.start(now);
    noiseSource.start(now);

    this.running = true;
    this.tickTimer = setInterval(() => this.tick(), TICK_SECONDS * 1000);
  }

  /** Fade out and tear the graph down. */
  stop(): void {
    if (!this.running || this.context === null) {
      return;
    }
    const context = this.context;
    const now = context.currentTime;
    if (this.masterGain !== null) {
      this.masterGain.gain.linearRampToValueAtTime(0, now + FADE_SECONDS);
    }
    const stopAt = now + FADE_SECONDS;
    this.oscillators.forEach((osc) => osc.stop(stopAt));
    this.breathLfo?.stop(stopAt);
    this.swellLfo?.stop(stopAt);
    this.noiseSource?.stop(stopAt);
    this.running = false;
    if (this.tickTimer !== null) {
      clearInterval(this.tickTimer);
      this.tickTimer = null;
    }
  }

  /** The volume slider, 0..1. Anything outside is clamped; the resulting
   * gain can never exceed MAX_MASTER_GAIN. */
  setVolume(volume: number): void {
    this.volume = Math.min(1, Math.max(0, volume));
    if (this.masterGain !== null && this.context !== null && this.running) {
      this.masterGain.gain.linearRampToValueAtTime(
        this.masterCeiling(),
        this.context.currentTime + 0.2,
      );
    }
  }

  /** A different mood: retarget the palette's ranges, do not rebuild. */
  setPalette(palette: SoundscapePalette): void {
    this.palette = palette;
    this.targetCutoff = (palette.cutoffRange[0] + palette.cutoffRange[1]) / 2;
    if (this.adaptationEnabled) {
      this.targetCutoff = brightnessForEnergy(palette.cutoffRange, this.energy);
    }
  }

  /** Lower texture, shallower breath, slower adaptation. */
  setReducedStimulation(enabled: boolean): void {
    this.reducedStimulation = enabled;
    if (this.noiseGain !== null) {
      this.noiseGain.gain.value = this.noiseLevel();
      this.bumpParam("noise-gain", { level: this.noiseLevel() });
    }
    if (this.lfoAmp !== null) {
      this.lfoAmp.gain.value = this.lfoDepth();
      this.bumpParam("lfo-amp", { depth: this.lfoDepth() });
    }
  }

  /** Opt-in adaptation: brightness then follows the self-reported energy. */
  setAdaptation(enabled: boolean): void {
    this.adaptationEnabled = enabled;
    if (enabled) {
      this.targetCutoff = brightnessForEnergy(this.palette.cutoffRange, this.energy);
    } else {
      this.targetCutoff = (this.palette.cutoffRange[0] + this.palette.cutoffRange[1]) / 2;
    }
  }

  /** Breaths per minute, from the pacer or the user's own count. */
  setPace(bpm: number): void {
    this.targetPace = clampPace(bpm);
  }

  /** Self-reported energy; honoured only while adaptation is on. */
  setEnergy(energy: EnergyLevel): void {
    this.energy = energy;
    if (this.adaptationEnabled) {
      this.targetCutoff = brightnessForEnergy(this.palette.cutoffRange, energy);
    }
  }

  /** One adaptation step. The interval drives it; tests call it directly. */
  tick(): void {
    if (!this.running) {
      return;
    }
    const lerp = ADAPT_LERP * (this.reducedStimulation ? 0.5 : 1);
    this.actualPace += (this.targetPace - this.actualPace) * lerp;
    this.actualCutoff += (this.targetCutoff - this.actualCutoff) * lerp;
    // The bounds are the promise: even a hostile target drifts inside them.
    this.actualPace = Math.min(MAX_PACE_BPM, Math.max(MIN_PACE_BPM, this.actualPace));
    this.actualCutoff = Math.min(
      this.palette.cutoffRange[1],
      Math.max(this.palette.cutoffRange[0], this.actualCutoff),
    );
    if (this.breathLfo !== null) {
      this.breathLfo.frequency.value = this.actualPace / 60;
    }
    if (this.swellLfo !== null) {
      this.swellLfo.frequency.value = this.actualPace / 120;
    }
    if (this.padFilter !== null) {
      this.padFilter.frequency.value = this.actualCutoff;
    }
  }

  /** The master gain the UI may never push past. */
  masterCeiling(): number {
    return this.volume * MAX_MASTER_GAIN;
  }

  /** A serialisable view of the graph — for tests and for the verification
   * report ("show the node graph + volume-limit assertion"). */
  describeGraph(): GraphDescription {
    return {
      engine: "manovia-soundscape-v1",
      running: this.running,
      paletteId: this.palette.id,
      reducedStimulation: this.reducedStimulation,
      adaptationEnabled: this.adaptationEnabled,
      master: {
        ceiling: MAX_MASTER_GAIN,
        currentGain: this.masterGain?.gain.value ?? 0,
        volume: this.volume,
      },
      limiter: LIMITER,
      nodes: [...this.nodeRegistry.values()],
      edges: [...this.edges],
      pace: {
        actualBpm: round2(this.actualPace),
        targetBpm: round2(this.targetPace),
        minBpm: MIN_PACE_BPM,
        maxBpm: MAX_PACE_BPM,
      },
      brightness: {
        actualHz: round2(this.actualCutoff),
        targetHz: round2(this.targetCutoff),
        minHz: this.palette.cutoffRange[0],
        maxHz: this.palette.cutoffRange[1],
      },
    };
  }

  /** Release the context entirely (page unmount). */
  async dispose(): Promise<void> {
    this.stop();
    if (this.context !== null) {
      await this.context.close();
      this.context = null;
    }
  }

  private register(
    id: string,
    type: string,
    node: AudioNodeLike,
    params: Record<string, string | number> = {},
  ): void {
    this.nodeRegistry.set(id, { id, type, params });
    void node;
  }

  /** Keep the registry's reported params in step with the live node. */
  private bumpParam(id: string, params: Record<string, string | number>): void {
    const node = this.nodeRegistry.get(id);
    if (node !== undefined) {
      node.params = { ...node.params, ...params };
    }
  }

  private connect(from: AudioNodeLike, to: AudioNodeLike, fromId: string, toId: string): void {
    from.connect(to);
    this.edges.push([fromId, toId]);
  }

  private noiseLevel(): number {
    return this.palette.textureLevel * (this.reducedStimulation ? 0.4 : 1) * 0.5;
  }

  private lfoDepth(): number {
    const span = this.palette.cutoffRange[1] - this.palette.cutoffRange[0];
    return this.palette.lfoDepth * span * 0.5 * (this.reducedStimulation ? 0.5 : 1);
  }
}

function round2(value: number): number {
  return Math.round(value * 100) / 100;
}

function browserContext(): SoundscapeContext {
  // The real AudioContext satisfies the structural interface.
  return new AudioContext() as unknown as SoundscapeContext;
}
