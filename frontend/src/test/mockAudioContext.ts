/**
 * A mocked Web Audio context for tests (Day 45).
 *
 * The meditation soundscape engine is written against a structural interface
 * (`SoundscapeContext`), so a test can build the exact same node graph — and
 * assert on it — without an audio device. Ramps apply immediately (mock
 * semantics), so assertions read final values directly.
 *
 * `MockAudioContext` doubles as a drop-in `window.AudioContext` stub: the
 * view tests replace the global with it, so the engine runs its real
 * `start()` path in jsdom.
 */

import type {
  AudioBufferLike,
  AudioParamLike,
  AudioNodeLike,
  BufferSourceNodeLike,
  CompressorNodeLike,
  FilterNodeLike,
  GainNodeLike,
  OscillatorNodeLike,
} from "@/features/meditation/soundscape/engine";

export class MockParam implements AudioParamLike {
  value: number;
  ramps: Array<{ to: number; when: number }> = [];

  constructor(initial: number) {
    this.value = initial;
  }

  setValueAtTime(value: number): void {
    this.value = value;
  }

  linearRampToValueAtTime(value: number, time: number): void {
    this.ramps.push({ to: value, when: time });
    this.value = value;
  }
}

export class MockNode implements AudioNodeLike {
  connected: string[] = [];
  readonly id: string;

  constructor(id: string) {
    this.id = id;
  }

  connect(destination: AudioNodeLike | AudioParamLike): AudioNodeLike | AudioParamLike {
    this.connected.push((destination as { id?: string }).id ?? "param");
    return destination;
  }
}

export interface SourceTimings {
  startCalls: number[];
  stopCalls: number[];
}

export class MockOsc extends MockNode implements OscillatorNodeLike {
  type = "sine" as const;
  frequency: MockParam;
  source: SourceTimings = { startCalls: [], stopCalls: [] };

  constructor(id: string, frequency: number) {
    super(id);
    this.frequency = new MockParam(frequency);
  }

  start(when = 0): void {
    this.source.startCalls.push(when);
  }

  stop(when = 0): void {
    this.source.stopCalls.push(when);
  }
}

export class MockGain extends MockNode implements GainNodeLike {
  gain: MockParam;

  constructor(id: string, level = 1) {
    super(id);
    this.gain = new MockParam(level);
  }
}

export class MockFilter extends MockNode implements FilterNodeLike {
  type: "lowpass" | "bandpass" = "lowpass";
  frequency: MockParam;
  Q: MockParam;

  constructor(id: string, frequency: number) {
    super(id);
    this.frequency = new MockParam(frequency);
    this.Q = new MockParam(1);
  }
}

export class MockCompressor extends MockNode implements CompressorNodeLike {
  threshold = new MockParam(0);
  knee = new MockParam(0);
  ratio = new MockParam(1);
  attack = new MockParam(0.003);
  release = new MockParam(0.25);
}

export class MockBufferSource extends MockNode implements BufferSourceNodeLike {
  loop = false;
  buffer: unknown = null;
  source: SourceTimings = { startCalls: [], stopCalls: [] };

  constructor(id: string) {
    super(id);
  }

  start(when = 0): void {
    this.source.startCalls.push(when);
  }

  stop(when = 0): void {
    this.source.stopCalls.push(when);
  }
}

export class MockBuffer implements AudioBufferLike {
  constructor(readonly length: number) {}

  getChannelData(): Float32Array {
    return new Float32Array(this.length);
  }
}

/** The mocked context; also usable as a `window.AudioContext` replacement. */
export class MockAudioContext {
  currentTime = 0;
  state = "running";
  readonly destination = new MockNode("destination");
  /** [length, sampleRate] of every buffer requested. */
  buffers: Array<[number, number]> = [];
  private seq = 0;

  async resume(): Promise<void> {}
  async close(): Promise<void> {}

  createOscillator(): OscillatorNodeLike {
    return new MockOsc(`osc-${(this.seq += 1)}`, 0);
  }

  createGain(): GainNodeLike {
    return new MockGain(`gain-${(this.seq += 1)}`);
  }

  createBiquadFilter(): FilterNodeLike {
    return new MockFilter(`filter-${(this.seq += 1)}`, 0);
  }

  createDynamicsCompressor(): CompressorNodeLike {
    return new MockCompressor(`compressor-${(this.seq += 1)}`);
  }

  createBufferSource(): BufferSourceNodeLike {
    return new MockBufferSource(`buffersource-${(this.seq += 1)}`);
  }

  createBuffer(_channels: number, length: number, sampleRate: number): AudioBufferLike {
    this.buffers.push([length, sampleRate]);
    return new MockBuffer(length);
  }
}
