import { describe, expect, it, vi } from "vitest";

import { CALM_AUDIO_GAIN, CalmAudio, type CalmAudioContext } from "./audio";

class FakeParam {
  value: number;
  setTargetAtTime = vi.fn((value: number) => {
    this.value = value;
  });

  constructor(value: number, register: FakeParam[]) {
    this.value = value;
    register.push(this);
  }
}

function fakeContext(): {
  context: CalmAudioContext;
  close: ReturnType<typeof vi.fn>;
  params: FakeParam[];
} {
  const params: FakeParam[] = [];
  class FakeNode {
    connect = vi.fn((destination: unknown) => destination);
    disconnect = vi.fn();
  }
  const oscillators: Array<
    FakeNode & { start: ReturnType<typeof vi.fn>; stop: ReturnType<typeof vi.fn> }
  > = [];
  const close = vi.fn(async () => undefined);
  const context: CalmAudioContext = {
    currentTime: 3,
    destination: new FakeNode() as never,
    createOscillator: () => {
      const oscillator = Object.assign(new FakeNode(), {
        type: "sine",
        frequency: new FakeParam(110, params),
        start: vi.fn(),
        stop: vi.fn(),
      });
      oscillators.push(oscillator);
      return oscillator as never;
    },
    createGain: () =>
      Object.assign(new FakeNode(), { gain: new FakeParam(CALM_AUDIO_GAIN, params) }) as never,
    createBiquadFilter: () =>
      Object.assign(new FakeNode(), {
        type: "lowpass",
        frequency: new FakeParam(520, params),
        Q: new FakeParam(0.4, params),
      }) as never,
    close,
  };
  return { context, close, params };
}

describe("procedural calm audio", () => {
  it("starts a soft, static pad only when asked", () => {
    const { context, params } = fakeContext();
    const audio = new CalmAudio(() => context);
    expect(audio.running).toBe(false);
    expect(audio.start()).toBe(true);
    expect(audio.running).toBe(true);
    // The pad is quiet by construction: master gain below 0.1, then a ramp to
    // the resting pad level rather than a step.
    expect(CALM_AUDIO_GAIN).toBeLessThan(0.1);
    expect(params[0]?.setTargetAtTime).toHaveBeenCalled();
    expect(params[0]?.value).toBeLessThan(CALM_AUDIO_GAIN);
  });

  it("moves the gain with a ramp, never a step", () => {
    const { context, params } = fakeContext();
    const audio = new CalmAudio(() => context);
    audio.start();
    const pad = params[0];
    audio.setBreathScale(1);
    expect(pad?.setTargetAtTime).toHaveBeenCalled();
    // Includes the time constant, so there is no audible click.
    expect(pad?.setTargetAtTime).toHaveBeenCalledWith(expect.any(Number), 3, 0.35);
    audio.setBreathScale(Number.NaN);
    audio.setBreathScale(-5);
    audio.setBreathScale(99);
  });

  it("stops once, closes the context, and is safe before and after", () => {
    const { context, close } = fakeContext();
    const audio = new CalmAudio(() => context);
    expect(() => {
      audio.setBreathScale(1);
      audio.stop();
    }).not.toThrow();
    audio.start();
    audio.stop();
    audio.stop();
    expect(close).toHaveBeenCalledTimes(1);
    expect(audio.running).toBe(false);
    expect(() => {
      audio.setBreathScale(1);
    }).not.toThrow();
  });

  it("is silent, not broken, when Web Audio is unavailable", () => {
    const audio = new CalmAudio(() => null);
    expect(audio.start()).toBe(false);
    expect(audio.running).toBe(false);
  });
});
