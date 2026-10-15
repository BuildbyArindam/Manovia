import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ApiClient, MemoryTokenStore } from "@/lib/api";
import { TEST_EMERGENCY, TEST_RESOURCES, crisisMessage } from "@/test/crisisFixtures";

import {
  AssistantAudioPlayer,
  type AudioBufferSourceLike,
  type AudioContextLike,
} from "./audioPlayback";
import { VoiceCompanion, type PcmRecorder, type VoiceSocket } from "./VoiceCompanion";
import type { ChatMetadata } from "./types";

class FakeSocket implements VoiceSocket {
  readyState = 1;
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent<unknown>) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  readonly sent: string[] = [];

  send(data: string): void {
    this.sent.push(data);
  }

  close(): void {
    this.readyState = 3;
  }

  open(): void {
    this.onopen?.(new Event("open"));
  }

  message(payload: unknown): void {
    this.onmessage?.(new MessageEvent("message", { data: JSON.stringify(payload) }));
  }
}

class FakeRecorder implements PcmRecorder {
  onPcm: ((pcm: string) => void) | null = null;
  onSignal: (() => void) | null = null;
  sampleRateHz: number | null = null;
  stopped = false;

  async start(
    targetSampleRateHz: number,
    onPcm: (pcm: string) => void,
    onSignal: () => void,
  ): Promise<void> {
    this.sampleRateHz = targetSampleRateHz;
    this.onPcm = onPcm;
    this.onSignal = onSignal;
  }

  async stop(): Promise<void> {
    this.stopped = true;
  }
}

class RejectedRecorder extends FakeRecorder {
  override async start(): Promise<void> {
    throw new Error("permission denied");
  }
}

class FakeSource implements AudioBufferSourceLike {
  buffer: AudioBuffer | null = null;
  onended: ((event: Event) => void) | null = null;
  stops = 0;

  connect(destination: AudioNode): AudioNode {
    return destination;
  }

  start(): void {}

  stop(): void {
    this.stops += 1;
    this.onended?.(new Event("ended"));
  }
}

class FakeAudioContext implements AudioContextLike {
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

function signedInClient(): ApiClient {
  const store = new MemoryTokenStore();
  const client = new ApiClient({
    tokens: store,
    fetchImpl: async () => new Response("{}", { status: 200 }),
  });
  client.applySession({
    access_token: "access-token",
    refresh_token: "refresh-token",
    expires_in: 900,
  });
  return client;
}

function crisisMetadata(): ChatMetadata {
  return {
    risk_level: "high",
    emotion: null,
    response_type: "crisis",
    resources: [...TEST_RESOURCES],
    emergency: TEST_EMERGENCY,
    crisis: crisisMessage(),
    check_in: null,
    degraded: false,
    persisted: false,
    about_someone_else: false,
    locale: "en",
    region: "US",
  };
}

describe("VoiceCompanion", () => {
  it("provides a big stop, mute, and a toggleable in-memory transcript", async () => {
    const user = userEvent.setup();
    render(
      <VoiceCompanion
        client={signedInClient()}
        locale="en"
        onOpenSession={async () => "session-1"}
      />,
    );

    expect(screen.getByTestId("voice-start")).toHaveTextContent("Start voice conversation");
    expect(screen.getByTestId("voice-transcript")).toHaveTextContent("not saved");

    await user.click(screen.getByTestId("mute-assistant"));
    expect(screen.getByTestId("mute-assistant")).toBeChecked();
    await user.click(screen.getByTestId("toggle-transcript"));
    expect(screen.queryByTestId("voice-transcript")).toBeNull();
  });

  it("streams PCM, stops audio on local speech, and renders cached crisis help", async () => {
    const user = userEvent.setup();
    const socket = new FakeSocket();
    const recorder = new FakeRecorder();
    const context = new FakeAudioContext();
    const player = new AssistantAudioPlayer(() => context);
    const openSession = vi.fn(async () => "session-1");
    render(
      <VoiceCompanion
        client={signedInClient()}
        locale="en"
        onOpenSession={openSession}
        socketFactory={() => socket}
        recorderFactory={() => recorder}
        player={player}
      />,
    );

    await user.click(screen.getByTestId("voice-start"));
    act(() => socket.open());
    expect(JSON.parse(socket.sent[0] ?? "{}")).toMatchObject({ type: "start", locale: "en" });

    await act(async () => {
      socket.message({
        type: "ready",
        sample_rate_hz: 48_000,
        silence_timeout_seconds: 45,
        barge_in_target_ms: 300,
      });
      socket.message({ type: "started", storage: "memory_only" });
    });
    await waitFor(() => expect(screen.getByTestId("voice-status")).toHaveTextContent("Listening"));
    expect(recorder.sampleRateHz).toBe(48_000);

    act(() => recorder.onPcm?.("pcm-frame"));
    expect(socket.sent.map((item) => JSON.parse(item).type)).toContain("audio");

    await act(async () => {
      socket.message({
        type: "assistant_audio",
        audio: btoa("wav"),
        mime_type: "audio/wav",
        text: "Hello.",
      });
    });
    await waitFor(() => expect(context.sources).toHaveLength(1));
    act(() => recorder.onSignal?.());
    expect(context.sources[0]?.stops).toBe(1);

    await act(async () => {
      socket.message({
        type: "crisis",
        metadata: crisisMetadata(),
        audio: btoa("cached crisis wav"),
        mime_type: "audio/wav",
        continue_or_end: true,
      });
    });

    expect(await screen.findByTestId("voice-crisis-card")).toHaveTextContent(crisisMessage().title);
    expect(screen.getByTestId("voice-crisis-continue")).toBeVisible();
    expect(screen.getByRole("link", { name: /call 911/i })).toHaveAttribute("href", "tel:911");

    await user.click(screen.getByTestId("voice-crisis-continue"));
    expect(socket.sent.map((item) => JSON.parse(item).type)).toContain("continue");
  });

  it("releases a denied microphone session and lets the person retry or type", async () => {
    const user = userEvent.setup();
    const firstSocket = new FakeSocket();
    const secondSocket = new FakeSocket();
    const sockets = [firstSocket, secondSocket];
    const recorders: PcmRecorder[] = [new RejectedRecorder(), new FakeRecorder()];
    render(
      <VoiceCompanion
        client={signedInClient()}
        locale="en"
        onOpenSession={async () => "session-1"}
        socketFactory={() => sockets.shift() ?? secondSocket}
        recorderFactory={() => recorders.shift() ?? new FakeRecorder()}
      />,
    );

    await user.click(screen.getByTestId("voice-start"));
    act(() => firstSocket.open());
    await act(async () => firstSocket.message({ type: "started", storage: "memory_only" }));

    expect(await screen.findByTestId("voice-fallback")).toHaveTextContent(
      "microphone is unavailable",
    );
    expect(firstSocket.readyState).toBe(3);

    await user.click(screen.getByTestId("voice-start"));
    act(() => secondSocket.open());
    await act(async () => secondSocket.message({ type: "started", storage: "memory_only" }));
    await waitFor(() => expect(screen.getByTestId("voice-status")).toHaveTextContent("Listening"));
  });

  it("stops in-progress audio and falls back to typing on a socket error before close", async () => {
    const user = userEvent.setup();
    const socket = new FakeSocket();
    const context = new FakeAudioContext();
    render(
      <VoiceCompanion
        client={signedInClient()}
        locale="en"
        onOpenSession={async () => "session-1"}
        socketFactory={() => socket}
        recorderFactory={() => new FakeRecorder()}
        player={new AssistantAudioPlayer(() => context)}
      />,
    );

    await user.click(screen.getByTestId("voice-start"));
    act(() => socket.open());
    await act(async () => socket.message({ type: "started", storage: "memory_only" }));
    await waitFor(() => expect(screen.getByTestId("voice-status")).toHaveTextContent("Listening"));
    await act(async () => {
      socket.message({
        type: "assistant_audio",
        audio: btoa("wav"),
        mime_type: "audio/wav",
        text: "Hi.",
      });
    });
    await waitFor(() => expect(context.sources).toHaveLength(1));

    act(() => socket.onerror?.(new Event("error")));

    expect(context.sources[0]?.stops).toBe(1);
    expect(await screen.findByTestId("voice-fallback")).toHaveTextContent("connection dropped");
    expect(screen.getByTestId("voice-status")).toHaveTextContent("needs attention");
  });
});
