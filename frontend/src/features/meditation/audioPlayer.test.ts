import { describe, expect, it } from "vitest";

import { MeditationAudioPlayer, activeCaption } from "./audioPlayer";
import type { AudioElementLike } from "./audioPlayer";
import type { Caption } from "./types";

class FakeAudio implements AudioElementLike {
  src = "";
  volume = 1;
  paused = true;
  currentTime = 0;
  listeners: Record<string, Array<() => void>> = {};
  static instances: FakeAudio[] = [];

  constructor() {
    FakeAudio.instances.push(this);
  }

  async play(): Promise<void> {
    this.paused = false;
  }

  pause(): void {
    this.paused = true;
  }

  load(): void {
    // Not needed in tests; present on the real element.
  }

  addEventListener(type: string, listener: () => void): void {
    (this.listeners[type] ??= []).push(listener);
  }

  removeEventListener(type: string, listener: () => void): void {
    const list = this.listeners[type] ?? [];
    this.listeners[type] = list.filter((fn) => fn !== listener);
  }

  fire(type: string): void {
    for (const fn of this.listeners[type] ?? []) {
      fn();
    }
  }
}

describe("MeditationAudioPlayer", () => {
  it("loads WAV bytes, plays, tracks captions, and stops instantly", async () => {
    const seen: number[] = [];
    let ended = false;
    const player = new MeditationAudioPlayer({
      createElement: () => new FakeAudio() as unknown as AudioElementLike,
      toObjectUrl: () => "blob:test",
    });
    player.onTime((t) => seen.push(t));
    player.onEnded(() => {
      ended = true;
    });

    player.load(new Uint8Array([44, 65, 87, 80]).buffer);
    expect(FakeAudio.instances).toHaveLength(1);
    expect(FakeAudio.instances[0]?.src).toBe("blob:test");
    expect(player.isPlaying).toBe(false);

    await player.play();
    expect(player.isPlaying).toBe(true);

    const element = FakeAudio.instances[0] as FakeAudio;
    element.currentTime = 1.5;
    element.fire("timeupdate");
    expect(seen).toEqual([1.5]);

    element.fire("ended");
    expect(ended).toBe(true);

    // Instant stop: paused now, source released, no residual state.
    player.stop();
    expect(player.isPlaying).toBe(false);
    expect(player.currentTime).toBe(0);
    // Re-loading replaces the source cleanly.
    player.load(new Uint8Array([1]).buffer);
    expect(FakeAudio.instances).toHaveLength(2);
    player.stop();
  });

  it("clamps voice volume like the soundscape clamps master gain", () => {
    const player = new MeditationAudioPlayer({
      createElement: () => new FakeAudio() as unknown as AudioElementLike,
    });
    player.load(new Uint8Array([1]).buffer);
    player.setVolume(5);
    expect((FakeAudio.instances.at(-1) as FakeAudio).volume).toBe(1);
    player.setVolume(-2);
    expect((FakeAudio.instances.at(-1) as FakeAudio).volume).toBe(0);
  });

  it("activeCaption returns the caption under the time cursor", () => {
    const captions: Caption[] = [
      { index: 0, section_id: "sec-1", start_s: 0, end_s: 2, text: "first" },
      { index: 1, section_id: "sec-2", start_s: 2, end_s: 5, text: "second" },
    ];
    expect(activeCaption(captions, 0)?.text).toBe("first");
    expect(activeCaption(captions, 2)?.text).toBe("second");
    expect(activeCaption(captions, 5)).toBeUndefined();
    expect(activeCaption([], 1)).toBeUndefined();
  });
});
