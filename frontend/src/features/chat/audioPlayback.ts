/** Local Web Audio playback for streamed assistant WAV chunks. */

import { base64ToArrayBuffer } from "./realtime";

export interface AudioBufferSourceLike {
  buffer: AudioBuffer | null;
  onended: ((event: Event) => void) | null;
  connect(destination: AudioNode): AudioNode;
  start(when?: number): void;
  stop(when?: number): void;
}

export interface AudioContextLike {
  destination: AudioNode;
  state: AudioContextState;
  resume(): Promise<void>;
  close(): Promise<void>;
  decodeAudioData(data: ArrayBuffer): Promise<AudioBuffer>;
  createBufferSource(): AudioBufferSourceLike;
}

export type AudioContextFactory = () => AudioContextLike;

function browserAudioContext(): AudioContextLike {
  return new AudioContext();
}

/**
 * Queues assistant chunks, and stops every scheduled source on barge-in/stop.
 *
 * `stop()` is synchronous from the button or first locally detected speech
 * frame: it never waits for a WebSocket round trip. That is how the client
 * reaches the 300 ms barge-in target even when the network is busy.
 */
export class AssistantAudioPlayer {
  private context: AudioContextLike | null = null;
  private readonly sources = new Set<AudioBufferSourceLike>();
  private queued: Promise<void> = Promise.resolve();
  private generation = 0;
  private muted = false;

  constructor(private readonly createContext: AudioContextFactory = browserAudioContext) {}

  setMuted(value: boolean): void {
    this.muted = value;
    if (value) {
      this.stop();
    }
  }

  async play(encodedWav: string): Promise<void> {
    const generation = this.generation;
    this.queued = this.queued
      .catch(() => undefined)
      .then(async () => {
        if (this.muted || generation !== this.generation) {
          return;
        }
        const context = this.ensureContext();
        if (context.state === "suspended") {
          await context.resume();
        }
        const decoded = await context.decodeAudioData(base64ToArrayBuffer(encodedWav));
        if (this.muted || generation !== this.generation) {
          return;
        }
        const source = context.createBufferSource();
        source.buffer = decoded;
        source.onended = () => {
          this.sources.delete(source);
        };
        source.connect(context.destination);
        this.sources.add(source);
        source.start(0);
      });
    return this.queued;
  }

  stop(): void {
    this.generation += 1;
    for (const source of this.sources) {
      try {
        source.stop(0);
      } catch {
        // A source that already ended throws in some engines; it is gone either way.
      }
    }
    this.sources.clear();
    this.queued = Promise.resolve();
  }

  async dispose(): Promise<void> {
    this.stop();
    const context = this.context;
    this.context = null;
    if (context !== null && context.state !== "closed") {
      await context.close();
    }
  }

  private ensureContext(): AudioContextLike {
    if (this.context === null) {
      this.context = this.createContext();
    }
    return this.context;
  }
}

/** Browser-local spoken fallback for a connection loss (there is no server then). */
export function speakLocalFallback(text: string, muted: boolean): void {
  if (muted || !("speechSynthesis" in window) || !("SpeechSynthesisUtterance" in window)) {
    return;
  }
  window.speechSynthesis.cancel();
  window.speechSynthesis.speak(new SpeechSynthesisUtterance(text));
}
