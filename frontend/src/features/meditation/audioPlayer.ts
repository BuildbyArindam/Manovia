import type { Caption } from "./types";

/**
 * Playback of the rendered meditation audio (a WAV from the local TTS
 * endpoint) with caption tracking.
 *
 * The spoken script and the soundscape are deliberately separate engines:
 * the voice is a plain media element (seekable, reliable, works offline
 * from the streamed bytes), the soundscape is the Web Audio graph. "Stop
 * instantly" calls {@link stop}, which is immediate by construction.
 */

export interface AudioElementLike {
  src: string;
  volume: number;
  readonly paused: boolean;
  readonly currentTime: number;
  play(): Promise<void>;
  pause(): void;
  load(): void;
  addEventListener(type: string, listener: () => void): void;
  removeEventListener(type: string, listener: () => void): void;
}

export type AudioElementFactory = () => AudioElementLike;

export interface PlayerOptions {
  createElement?: AudioElementFactory;
  toObjectUrl?: (blob: Blob) => string;
  revokeObjectUrl?: (url: string) => void;
}

export class MeditationAudioPlayer {
  private element: AudioElementLike | null = null;
  private objectUrl: string | null = null;
  private onTimeListener: ((t: number) => void) | null = null;
  private onEndListener: (() => void) | null = null;
  private readonly createElement: AudioElementFactory;
  private readonly toObjectUrl: (blob: Blob) => string;
  private readonly revokeObjectUrl: (url: string) => void;

  constructor(options: PlayerOptions = {}) {
    this.createElement =
      options.createElement ??
      (() => new Audio() as unknown as AudioElementLike);
    this.toObjectUrl = options.toObjectUrl ?? ((blob) => URL.createObjectURL(blob));
    this.revokeObjectUrl = options.revokeObjectUrl ?? ((url) => URL.revokeObjectURL(url));
  }

  get isPlaying(): boolean {
    return this.element !== null && !this.element.paused;
  }

  get currentTime(): number {
    return this.element?.currentTime ?? 0;
  }

  /** Replace the current source (renders arrive as raw WAV bytes). */
  load(bytes: ArrayBuffer): void {
    this.stopInternal();
    const blob = new Blob([bytes], { type: "audio/wav" });
    this.objectUrl = this.toObjectUrl(blob);
    const element = this.createElement();
    element.src = this.objectUrl;
    element.addEventListener("timeupdate", this.handleTime);
    element.addEventListener("ended", this.handleEnded);
    this.element = element;
  }

  async play(): Promise<void> {
    if (this.element === null) {
      return;
    }
    await this.element.play();
  }

  pause(): void {
    this.element?.pause();
  }

  /** Instant stop: paused and rewound now, no fade. This is the
   * "you can stop at any time" control, wired to the UI's stop button. */
  stop(): void {
    this.stopInternal();
  }

  setVolume(volume: number): void {
    if (this.element !== null) {
      this.element.volume = Math.min(1, Math.max(0, volume));
    }
  }

  onTime(listener: (t: number) => void): void {
    this.onTimeListener = listener;
  }

  onEnded(listener: () => void): void {
    this.onEndListener = listener;
  }

  private stopInternal(): void {
    if (this.element !== null) {
      this.element.pause();
      this.element.removeEventListener("timeupdate", this.handleTime);
      this.element.removeEventListener("ended", this.handleEnded);
      this.element = null;
    }
    if (this.objectUrl !== null) {
      this.revokeObjectUrl(this.objectUrl);
      this.objectUrl = null;
    }
  }

  private handleTime = (): void => {
    if (this.element !== null && this.onTimeListener !== null) {
      this.onTimeListener(this.element.currentTime);
    }
  };

  private handleEnded = (): void => {
    if (this.onEndListener !== null) {
      this.onEndListener();
    }
  };
}

/** The caption under the cursor, for the live transcript. */
export function activeCaption(
  captions: readonly Caption[],
  time: number,
): Caption | undefined {
  return captions.find((caption) => time >= caption.start_s && time < caption.end_s);
}
