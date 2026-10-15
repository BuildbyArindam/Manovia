/**
 * The Day 40 real-time voice companion controls.
 *
 * Audio frames and transcript rows are React memory for this open socket only:
 * this component never uses localStorage, IndexedDB, an upload endpoint, or a
 * persistence switch.  The server receives words through the same crisis-first
 * chat orchestrator used by typing.  The PCM gate below is only a speech/silence
 * transport aid; no voice signal is used to infer emotion or a health state.
 */

import { useCallback, useEffect, useRef, useState, type ReactElement } from "react";

import { CrisisCard } from "@/components/CrisisCard";
import type { ApiClient } from "@/lib/api";

import { AssistantAudioPlayer, speakLocalFallback } from "./audioPlayback";
import {
  VOICE_SAMPLE_RATE_HZ,
  VOICE_SUBPROTOCOL,
  floatToPcm16,
  parseVoiceServerEvent,
  pcm16ToBase64,
  pcmHasSignal,
  resampleMono,
  voiceWebSocketUrl,
  type VoiceLatency,
} from "./realtime";
import type { ChatLocale, ChatMetadata } from "./types";

const NETWORK_FALLBACK = "The connection dropped. You can type instead if that is easier.";
const MICROPHONE_FALLBACK =
  "Your microphone is unavailable. You can type instead if that is easier.";

export interface VoiceSocket {
  readonly readyState: number;
  onopen: ((event: Event) => void) | null;
  onmessage: ((event: MessageEvent<unknown>) => void) | null;
  onerror: ((event: Event) => void) | null;
  onclose: ((event: CloseEvent) => void) | null;
  send(data: string): void;
  close(code?: number, reason?: string): void;
}

export type VoiceSocketFactory = (url: string, protocols: string[]) => VoiceSocket;

function browserSocket(url: string, protocols: string[]): VoiceSocket {
  return new WebSocket(url, protocols);
}

export interface PcmRecorder {
  start(onPcm: (pcm: string) => void, onSignal: () => void): Promise<void>;
  stop(): Promise<void>;
}

export type PcmRecorderFactory = () => PcmRecorder;

class BrowserPcmRecorder implements PcmRecorder {
  private stream: MediaStream | null = null;
  private context: AudioContext | null = null;
  private processor: ScriptProcessorNode | null = null;
  private sink: GainNode | null = null;

  async start(onPcm: (pcm: string) => void, onSignal: () => void): Promise<void> {
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: false,
      },
    });
    const context = new AudioContext();
    const source = context.createMediaStreamSource(this.stream);
    // ScriptProcessor is used rather than MediaRecorder because the protocol
    // needs short PCM frames for server VAD and a prompt barge-in. It is
    // connected through a zero-gain node solely to keep processing active.
    const processor = context.createScriptProcessor(2048, 1, 1);
    const sink = context.createGain();
    sink.gain.value = 0;
    processor.onaudioprocess = (event) => {
      const input = event.inputBuffer.getChannelData(0);
      const frame = resampleMono(input, context.sampleRate, VOICE_SAMPLE_RATE_HZ);
      if (pcmHasSignal(frame)) {
        onSignal();
      }
      onPcm(pcm16ToBase64(floatToPcm16(frame)));
    };
    source.connect(processor);
    processor.connect(sink);
    sink.connect(context.destination);
    this.context = context;
    this.processor = processor;
    this.sink = sink;
  }

  async stop(): Promise<void> {
    this.processor?.disconnect();
    this.sink?.disconnect();
    this.stream?.getTracks().forEach((track) => track.stop());
    this.processor = null;
    this.sink = null;
    this.stream = null;
    const context = this.context;
    this.context = null;
    if (context !== null && context.state !== "closed") {
      await context.close();
    }
  }
}

function browserRecorder(): PcmRecorder {
  return new BrowserPcmRecorder();
}

type VoiceStatus = "idle" | "connecting" | "listening" | "replying" | "paused" | "failed";

interface TranscriptLine {
  id: number;
  speaker: "user" | "assistant";
  text: string;
  final: boolean;
}

export interface VoiceCompanionProps {
  client: ApiClient;
  locale: ChatLocale;
  disabled?: boolean;
  /** Opens the same privacy-default ephemeral chat session that typing uses. */
  onOpenSession: () => Promise<string | null>;
  socketFactory?: VoiceSocketFactory;
  recorderFactory?: PcmRecorderFactory;
  player?: AssistantAudioPlayer;
}

/**
 * Voice controls sit beside chat rather than replacing it: typing remains a
 * first-class fallback during a network/STT/LLM failure and for people who do
 * not want to grant microphone permission.
 */
export function VoiceCompanion({
  client,
  locale,
  disabled = false,
  onOpenSession,
  socketFactory = browserSocket,
  recorderFactory = browserRecorder,
  player: providedPlayer,
}: VoiceCompanionProps): ReactElement {
  const playerRef = useRef<AssistantAudioPlayer>(providedPlayer ?? new AssistantAudioPlayer());
  const socketRef = useRef<VoiceSocket | null>(null);
  const recorderRef = useRef<PcmRecorder | null>(null);
  const activeRef = useRef(false);
  const endingRef = useRef(false);
  const mutedRef = useRef(false);
  const lineSequence = useRef(0);

  const [status, setStatus] = useState<VoiceStatus>("idle");
  const [muted, setMuted] = useState(false);
  const [showTranscript, setShowTranscript] = useState(true);
  const [lines, setLines] = useState<TranscriptLine[]>([]);
  const [failure, setFailure] = useState<string | null>(null);
  const [crisis, setCrisis] = useState<ChatMetadata | null>(null);
  const [latency, setLatency] = useState<VoiceLatency | null>(null);

  const updateLine = useCallback(
    (speaker: "user" | "assistant", text: string, final: boolean): void => {
      setLines((previous) => {
        const last = previous.at(-1);
        if (last !== undefined && last.speaker === speaker && !last.final) {
          return [...previous.slice(0, -1), { ...last, text, final }];
        }
        lineSequence.current += 1;
        return [...previous, { id: lineSequence.current, speaker, text, final }];
      });
    },
    [],
  );

  const stopLocalMedia = useCallback(async (): Promise<void> => {
    playerRef.current.stop();
    const recorder = recorderRef.current;
    recorderRef.current = null;
    if (recorder !== null) {
      await recorder.stop();
    }
  }, []);

  const stop = useCallback(
    async (clearTranscript = true): Promise<void> => {
      endingRef.current = true;
      activeRef.current = false;
      const socket = socketRef.current;
      socketRef.current = null;
      if (socket !== null) {
        try {
          if (socket.readyState === WebSocket.OPEN) {
            socket.send(JSON.stringify({ type: "end" }));
          }
          socket.close(1000, "voice session ended");
        } catch {
          // The connection has already gone; local cleanup still matters.
        }
      }
      await stopLocalMedia();
      if (clearTranscript) {
        setLines([]);
        setCrisis(null);
        setLatency(null);
      }
      setStatus("idle");
    },
    [stopLocalMedia],
  );

  useEffect(() => {
    return () => {
      void stop();
    };
  }, [stop]);

  const setAssistantMuted = useCallback((value: boolean): void => {
    mutedRef.current = value;
    setMuted(value);
    playerRef.current.setMuted(value);
  }, []);

  const start = useCallback(async (): Promise<void> => {
    if (disabled || activeRef.current) {
      return;
    }
    setFailure(null);
    setCrisis(null);
    setLatency(null);
    setLines([]);
    lineSequence.current = 0;
    setStatus("connecting");
    endingRef.current = false;

    let sessionId: string | null;
    let token: string | null;
    try {
      sessionId = await onOpenSession();
      token = await client.accessTokenForWebSocket();
    } catch {
      setFailure("Voice could not start. You can type instead if that is easier.");
      setStatus("failed");
      return;
    }
    if (sessionId === null || token === null) {
      setFailure("Please sign in and accept the companion notice before starting voice.");
      setStatus("failed");
      return;
    }

    let socket: VoiceSocket;
    try {
      socket = socketFactory(voiceWebSocketUrl(sessionId, window.location), [
        VOICE_SUBPROTOCOL,
        `bearer.${token}`,
      ]);
    } catch {
      setFailure(NETWORK_FALLBACK);
      setStatus("failed");
      speakLocalFallback(NETWORK_FALLBACK, mutedRef.current);
      return;
    }
    socketRef.current = socket;
    activeRef.current = true;

    socket.onopen = () => {
      socket.send(JSON.stringify({ type: "start", locale }));
    };
    socket.onmessage = (message) => {
      const event = parseVoiceServerEvent(
        typeof message.data === "string" ? safeJson(message.data) : message.data,
      );
      if (event === null) {
        return;
      }
      switch (event.type) {
        case "ready":
          return;
        case "started": {
          const recorder = recorderFactory();
          recorderRef.current = recorder;
          void recorder
            .start(
              (pcm) => {
                // The first non-silent local frame stops assistant playback
                // synchronously; the matching PCM packet tells the server to
                // cancel generation too.
                const open = socketRef.current;
                if (activeRef.current && open !== null && open.readyState === WebSocket.OPEN) {
                  open.send(JSON.stringify({ type: "audio", pcm }));
                }
              },
              () => {
                playerRef.current.stop();
              },
            )
            .then(() => setStatus("listening"))
            .catch(() => {
              // A rejected permission prompt can occur after the socket was
              // accepted. Release that socket so a later Start click is a new,
              // usable attempt rather than a stuck failed session.
              activeRef.current = false;
              endingRef.current = true;
              socketRef.current = null;
              try {
                socket.close(1000, "microphone unavailable");
              } catch {
                // A failed browser socket still has no local audio to retain.
              }
              void stopLocalMedia();
              setFailure(MICROPHONE_FALLBACK);
              setStatus("failed");
              speakLocalFallback(MICROPHONE_FALLBACK, mutedRef.current);
            });
          return;
        }
        case "speech_started":
          setStatus("listening");
          return;
        case "transcript":
          updateLine(event.speaker, event.text, event.final);
          if (event.speaker === "assistant") {
            setStatus("replying");
          }
          return;
        case "assistant_audio":
          setStatus("replying");
          void playerRef.current.play(event.audio).catch(() => {
            setFailure("Audio could not play. You can read the transcript or type instead.");
          });
          return;
        case "barge_in":
          playerRef.current.stop();
          setStatus("listening");
          return;
        case "crisis":
          playerRef.current.stop();
          setCrisis(event.metadata);
          setStatus("paused");
          void playerRef.current.play(event.audio).catch(() => {
            setFailure("The crisis message is on screen. You can use the support options below.");
          });
          return;
        case "fallback":
          setFailure(event.message);
          // Keep the Stop control visible until the person chooses whether to
          // try again, end, or use typing; do not leave live media hidden.
          setStatus("paused");
          return;
        case "reply_final":
          updateLine("assistant", event.reply, true);
          setLatency(event.latency);
          if (event.metadata.response_type !== "crisis") {
            setStatus("listening");
          }
          return;
        case "timeout":
          setFailure(event.message);
          setStatus("paused");
          activeRef.current = false;
          void stopLocalMedia();
          return;
        case "error":
          setFailure("Voice could not continue. You can type instead.");
          setStatus("failed");
          return;
        case "continue_ready":
          setCrisis(null);
          setStatus("listening");
          return;
        case "ended":
        case "pong":
          return;
      }
    };
    const networkDrop = (): void => {
      // A few browsers surface only `error` for a disrupted upgrade while
      // others send error then close. Flip the ref first so this apology is
      // audible exactly once and media is released either way.
      if (endingRef.current || !activeRef.current) {
        return;
      }
      activeRef.current = false;
      socketRef.current = null;
      playerRef.current.stop();
      const recorder = recorderRef.current;
      recorderRef.current = null;
      void recorder?.stop();
      setFailure(NETWORK_FALLBACK);
      setStatus("failed");
      speakLocalFallback(NETWORK_FALLBACK, mutedRef.current);
    };
    socket.onerror = networkDrop;
    socket.onclose = networkDrop;
  }, [
    client,
    disabled,
    locale,
    onOpenSession,
    recorderFactory,
    socketFactory,
    stopLocalMedia,
    updateLine,
  ]);

  const continueAfterCrisis = useCallback((): void => {
    const socket = socketRef.current;
    if (socket !== null && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: "continue" }));
    }
  }, []);

  const active = status !== "idle" && status !== "failed";
  const crisisMessage = crisis?.crisis ?? null;

  return (
    <section
      aria-labelledby="voice-companion-heading"
      data-testid="voice-companion"
      className="rounded-2xl border-2 border-border-strong bg-surface p-4 shadow-soft"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 id="voice-companion-heading" className="text-lg font-semibold">
            Talk with Manovia
          </h2>
          <p className="mt-1 text-sm text-ink-muted">
            Live audio and the transcript stay in this session only. They are not saved unless you
            choose to save a chat later.
          </p>
        </div>
        <p
          role="status"
          data-testid="voice-status"
          className="text-sm font-semibold text-ink-muted"
        >
          {statusLabel(status)}
        </p>
      </div>

      <div className="mt-4 flex flex-wrap gap-3">
        {active ? (
          <button
            type="button"
            data-testid="voice-stop"
            onClick={() => void stop()}
            className="min-h-16 rounded-xl bg-danger px-6 py-3 text-lg font-bold text-white focus-visible:outline-2"
          >
            Stop voice conversation
          </button>
        ) : (
          <button
            type="button"
            data-testid="voice-start"
            disabled={disabled}
            onClick={() => void start()}
            className="min-h-16 rounded-xl bg-accent-bg px-6 py-3 text-lg font-bold text-accent-fg disabled:opacity-50"
          >
            Start voice conversation
          </button>
        )}

        <label className="inline-flex min-h-12 items-center gap-3 rounded-xl border border-border-strong px-4 py-2">
          <input
            type="checkbox"
            role="switch"
            data-testid="mute-assistant"
            checked={muted}
            onChange={(event) => setAssistantMuted(event.target.checked)}
            className="h-5 w-5 accent-accent-bg"
          />
          <span className="font-semibold">Mute assistant</span>
        </label>

        <button
          type="button"
          data-testid="toggle-transcript"
          aria-expanded={showTranscript}
          onClick={() => setShowTranscript((value) => !value)}
          className="min-h-12 rounded-xl border border-border-strong px-4 py-2 font-semibold"
        >
          {showTranscript ? "Hide transcript" : "Show transcript"}
        </button>
      </div>

      <p className="mt-3 text-sm text-ink-muted">
        Start speaking to interrupt Manovia. We use your words, not voice characteristics, to
        respond. If the connection or audio fails, use the text box below.
      </p>

      {failure === null ? null : (
        <div
          role="status"
          data-testid="voice-fallback"
          className="mt-4 rounded-xl border border-border-strong bg-surface-muted p-3"
        >
          <p className="font-semibold">Voice paused</p>
          <p className="mt-1 text-sm text-ink-muted">{failure}</p>
          <p className="mt-1 text-sm text-ink-muted">You can type a message below.</p>
        </div>
      )}

      {showTranscript ? (
        <div
          data-testid="voice-transcript"
          aria-label="Voice transcript for this open session"
          className="mt-4 max-h-56 overflow-y-auto rounded-xl bg-surface-muted p-3"
        >
          {lines.length === 0 ? (
            <p className="text-sm text-ink-muted">
              The live transcript will appear here. It is not saved.
            </p>
          ) : (
            <ol className="space-y-2">
              {lines.map((line) => (
                <li key={line.id} className="text-sm">
                  <span className="font-semibold">
                    {line.speaker === "user" ? "You" : "Manovia"}:{" "}
                  </span>
                  <span>{line.text}</span>
                  {line.final ? null : <span className="text-ink-muted"> …</span>}
                </li>
              ))}
            </ol>
          )}
        </div>
      ) : null}

      {crisis !== null ? (
        <div data-testid="voice-crisis" className="mt-4">
          <CrisisCard
            message={crisisMessage}
            resources={crisis.resources}
            emergency={crisis.emergency}
            disclaimer={crisisMessage?.disclaimer ?? null}
            testId="voice-crisis-card"
          />
          <div className="mt-3 flex flex-wrap gap-3">
            <button
              type="button"
              data-testid="voice-crisis-continue"
              onClick={continueAfterCrisis}
              className="min-h-12 rounded-xl bg-accent-bg px-4 py-2 font-semibold text-accent-fg"
            >
              Continue listening
            </button>
            <button
              type="button"
              data-testid="voice-crisis-end"
              onClick={() => void stop()}
              className="min-h-12 rounded-xl border border-border-strong px-4 py-2 font-semibold"
            >
              End voice session
            </button>
          </div>
        </div>
      ) : null}

      {latency === null ? null : (
        <p data-testid="voice-latency" className="mt-3 text-xs text-ink-muted">
          Time to first audio: {formatLatency(latency.time_to_first_audio_ms)} ms · server VAD:{" "}
          {formatLatency(latency.vad_ms)} ms
        </p>
      )}
    </section>
  );
}

function safeJson(value: string): unknown {
  try {
    return JSON.parse(value);
  } catch {
    return null;
  }
}

function statusLabel(status: VoiceStatus): string {
  switch (status) {
    case "idle":
      return "Voice is off";
    case "connecting":
      return "Connecting voice…";
    case "listening":
      return "Listening";
    case "replying":
      return "Manovia is replying";
    case "paused":
      return "Voice is paused";
    case "failed":
      return "Voice needs attention";
  }
}

function formatLatency(value: number | null): string {
  return value === null ? "—" : value.toFixed(0);
}
