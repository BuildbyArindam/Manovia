/**
 * The small, browser-only half of the Day 40 voice WebSocket protocol.
 *
 * PCM is used only to transport microphone samples and to find the first
 * non-silent frame for local barge-in. It is never used to label emotion,
 * health, identity, mood, or any mental state. Words from server STT remain the
 * only input to the companion's safety and chat pipeline.
 */

import type { ChatMetadata, ChatLocale } from "./types";

export const VOICE_SUBPROTOCOL = "manovia.v1";
export const VOICE_SAMPLE_RATE_HZ = 16_000;

export type VoiceSpeaker = "user" | "assistant";

export type VoiceServerEvent =
  | {
      type: "ready";
      sample_rate_hz: number;
      silence_timeout_seconds: number;
      barge_in_target_ms: number;
    }
  | { type: "started"; storage: "memory_only" }
  | { type: "speech_started" }
  | { type: "transcript"; speaker: VoiceSpeaker; text: string; final: boolean }
  | { type: "assistant_audio"; audio: string; mime_type: string; text: string }
  | { type: "barge_in"; stop_audio: true; server_ack_ms: number; target_ms: number }
  | {
      type: "crisis";
      metadata: ChatMetadata;
      audio: string;
      mime_type: string;
      continue_or_end: true;
    }
  | { type: "fallback"; code: string; message: string }
  | { type: "reply_final"; reply: string; metadata: ChatMetadata; latency: VoiceLatency }
  | { type: "timeout"; message: string }
  | { type: "continue_ready" }
  | { type: "ended" }
  | { type: "error"; code: string }
  | { type: "pong" };

export interface VoiceLatency {
  vad_ms: number;
  stt_first_partial_ms: number | null;
  stt_final_ms: number | null;
  llm_first_token_ms: number | null;
  tts_first_chunk_ms: number | null;
  time_to_first_audio_ms: number | null;
}

export type VoiceClientEvent =
  | { type: "start"; locale: ChatLocale; region?: string | null }
  | { type: "audio"; pcm: string }
  | { type: "finish" }
  | { type: "continue" }
  | { type: "end" }
  | { type: "ping" };

/** Build a same-origin `ws:`/`wss:` URL; no microphone payload appears in it. */
export function voiceWebSocketUrl(sessionId: string, location: Pick<Location, "origin">): string {
  const url = new URL(
    `/api/v1/voice/sessions/${encodeURIComponent(sessionId)}/stream`,
    location.origin,
  );
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
}

/** Convert 32-bit Web Audio samples into signed 16-bit PCM without retaining them. */
export function floatToPcm16(samples: Float32Array): Int16Array {
  const output = new Int16Array(samples.length);
  for (let index = 0; index < samples.length; index += 1) {
    const sample = Math.max(-1, Math.min(1, samples[index] ?? 0));
    output[index] = sample < 0 ? Math.round(sample * 0x8000) : Math.round(sample * 0x7fff);
  }
  return output;
}

/** A tiny linear resampler for transport compatibility, not an audio analysis model. */
export function resampleMono(
  samples: Float32Array,
  inputRate: number,
  outputRate: number,
): Float32Array {
  if (inputRate === outputRate) {
    return samples.slice();
  }
  const outputLength = Math.max(1, Math.round((samples.length * outputRate) / inputRate));
  const output = new Float32Array(outputLength);
  const ratio = inputRate / outputRate;
  for (let index = 0; index < outputLength; index += 1) {
    const position = index * ratio;
    const lower = Math.floor(position);
    const upper = Math.min(lower + 1, samples.length - 1);
    const fraction = position - lower;
    const left = samples[lower] ?? 0;
    const right = samples[upper] ?? left;
    output[index] = left + (right - left) * fraction;
  }
  return output;
}

/** Encode PCM bytes for JSON WebSocket framing. */
export function pcm16ToBase64(pcm: Int16Array): string {
  const bytes = new Uint8Array(pcm.buffer, pcm.byteOffset, pcm.byteLength);
  let binary = "";
  for (const byte of bytes) {
    binary += String.fromCharCode(byte);
  }
  return btoa(binary);
}

/** Decode a WAV chunk the local backend sent; audio never goes to storage. */
export function base64ToArrayBuffer(encoded: string): ArrayBuffer {
  const binary = atob(encoded);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return bytes.buffer;
}

/**
 * Signal/no-signal gate for immediate local barge-in only.
 *
 * It returns one boolean and intentionally exposes no reusable acoustic
 * features. The threshold is not a diagnosis or emotional inference.
 */
export function pcmHasSignal(samples: Float32Array, threshold = 0.02): boolean {
  return samples.some((sample) => Math.abs(sample) >= threshold);
}

export function parseVoiceServerEvent(value: unknown): VoiceServerEvent | null {
  if (!isRecord(value) || typeof value.type !== "string") {
    return null;
  }
  switch (value.type) {
    case "ready":
      return typeof value.sample_rate_hz === "number" &&
        typeof value.silence_timeout_seconds === "number" &&
        typeof value.barge_in_target_ms === "number"
        ? {
            type: "ready",
            sample_rate_hz: value.sample_rate_hz,
            silence_timeout_seconds: value.silence_timeout_seconds,
            barge_in_target_ms: value.barge_in_target_ms,
          }
        : null;
    case "started":
      return value.storage === "memory_only" ? { type: "started", storage: "memory_only" } : null;
    case "speech_started":
      return { type: "speech_started" };
    case "transcript":
      return isSpeaker(value.speaker) &&
        typeof value.text === "string" &&
        typeof value.final === "boolean"
        ? { type: "transcript", speaker: value.speaker, text: value.text, final: value.final }
        : null;
    case "assistant_audio":
      return typeof value.audio === "string" &&
        typeof value.mime_type === "string" &&
        typeof value.text === "string"
        ? {
            type: "assistant_audio",
            audio: value.audio,
            mime_type: value.mime_type,
            text: value.text,
          }
        : null;
    case "barge_in":
      return value.stop_audio === true &&
        typeof value.server_ack_ms === "number" &&
        typeof value.target_ms === "number"
        ? {
            type: "barge_in",
            stop_audio: true,
            server_ack_ms: value.server_ack_ms,
            target_ms: value.target_ms,
          }
        : null;
    case "crisis":
      return typeof value.audio === "string" &&
        typeof value.mime_type === "string" &&
        value.continue_or_end === true &&
        isRecord(value.metadata)
        ? {
            type: "crisis",
            metadata: value.metadata as unknown as ChatMetadata,
            audio: value.audio,
            mime_type: value.mime_type,
            continue_or_end: true,
          }
        : null;
    case "fallback":
      return typeof value.code === "string" && typeof value.message === "string"
        ? { type: "fallback", code: value.code, message: value.message }
        : null;
    case "reply_final":
      return typeof value.reply === "string" &&
        isRecord(value.metadata) &&
        isVoiceLatency(value.latency)
        ? {
            type: "reply_final",
            reply: value.reply,
            metadata: value.metadata as unknown as ChatMetadata,
            latency: value.latency,
          }
        : null;
    case "timeout":
      return typeof value.message === "string" ? { type: "timeout", message: value.message } : null;
    case "continue_ready":
    case "ended":
    case "pong":
      return { type: value.type };
    case "error":
      return typeof value.code === "string" ? { type: "error", code: value.code } : null;
    default:
      return null;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isSpeaker(value: unknown): value is VoiceSpeaker {
  return value === "user" || value === "assistant";
}

function isVoiceLatency(value: unknown): value is VoiceLatency {
  if (!isRecord(value) || typeof value.vad_ms !== "number") {
    return false;
  }
  return [
    value.stt_first_partial_ms,
    value.stt_final_ms,
    value.llm_first_token_ms,
    value.tts_first_chunk_ms,
    value.time_to_first_audio_ms,
  ].every((item) => typeof item === "number" || item === null);
}
