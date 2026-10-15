import { describe, expect, it } from "vitest";

import {
  AssistantAudioPlayer,
  type AudioBufferSourceLike,
  type AudioContextLike,
} from "./audioPlayback";

class FakeSource implements AudioBufferSourceLike {
  buffer: AudioBuffer | null = null;
  onended: ((event: Event) => void) | null = null;
  starts = 0;
  stops = 0;

  connect(destination: AudioNode): AudioNode {
    return destination;
  }

  start(): void {
    this.starts += 1;
  }

  stop(): void {
    this.stops += 1;
    this.onended?.(new Event("ended"));
  }
}

class FakeContext implements AudioContextLike {
  destination = {} as AudioNode;
  state: AudioContextState = "running";
  readonly sources: FakeSource[] = [];

  async resume(): Promise<void> {}

  async close(): Promise<void> {
    this.state = "closed";
  }

  async decodeAudioData(): Promise<AudioBuffer> {
    return {} as AudioBuffer;
  }

  createBufferSource(): AudioBufferSourceLike {
    const source = new FakeSource();
    this.sources.push(source);
    return source;
  }
}

describe("AssistantAudioPlayer", () => {
  it("stops scheduled assistant audio synchronously for a barge-in", async () => {
    const context = new FakeContext();
    const player = new AssistantAudioPlayer(() => context);

    await player.play(btoa("test wav bytes"));
    player.stop();

    expect(context.sources).toHaveLength(1);
    expect(context.sources[0]?.starts).toBe(1);
    expect(context.sources[0]?.stops).toBe(1);
  });

  it("does not start new audio while assistant mute is on", async () => {
    const context = new FakeContext();
    const player = new AssistantAudioPlayer(() => context);

    player.setMuted(true);
    await player.play(btoa("test wav bytes"));

    expect(context.sources).toHaveLength(0);
  });
});
