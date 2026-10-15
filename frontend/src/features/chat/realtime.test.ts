import { describe, expect, it } from "vitest";

import {
  base64ToArrayBuffer,
  floatToPcm16,
  parseVoiceServerEvent,
  pcm16ToBase64,
  pcmHasSignal,
  resampleMono,
  voiceWebSocketUrl,
} from "./realtime";

describe("real-time voice protocol helpers", () => {
  it("uses a same-origin secure WebSocket URL without putting a token or audio in it", () => {
    const url = voiceWebSocketUrl("session/a", { origin: "https://manovia.example" });

    expect(url).toBe("wss://manovia.example/api/v1/voice/sessions/session%2Fa/stream");
    expect(url).not.toContain("token");
    expect(url).not.toContain("pcm");
  });

  it("converts and base64-wraps PCM frames without retaining an audio blob", () => {
    const pcm = floatToPcm16(new Float32Array([-1, -0.5, 0, 0.5, 1]));
    const encoded = pcm16ToBase64(pcm);
    const bytes = new Int16Array(base64ToArrayBuffer(encoded));

    expect([...pcm]).toEqual([-32768, -16384, 0, 16384, 32767]);
    expect([...bytes]).toEqual([...pcm]);
  });

  it("resamples only for transport and exposes a simple signal/no-signal gate", () => {
    const frame = new Float32Array([0, 0.1, 0.2, 0.3]);

    expect(resampleMono(frame, 8_000, 16_000)).toHaveLength(8);
    expect(pcmHasSignal(new Float32Array([0, 0.01]))).toBe(false);
    expect(pcmHasSignal(new Float32Array([0, 0.03]))).toBe(true);
  });

  it("accepts known events and safely ignores malformed server data", () => {
    expect(
      parseVoiceServerEvent({
        type: "barge_in",
        stop_audio: true,
        server_ack_ms: 12,
        target_ms: 300,
      }),
    ).toEqual({ type: "barge_in", stop_audio: true, server_ack_ms: 12, target_ms: 300 });
    expect(parseVoiceServerEvent({ type: "assistant_audio", audio: 1 })).toBeNull();
    expect(parseVoiceServerEvent("not an event")).toBeNull();
  });
});
