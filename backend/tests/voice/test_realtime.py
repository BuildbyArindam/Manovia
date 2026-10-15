"""Scripted timing tests for Day 40's in-memory real-time voice session."""

from __future__ import annotations

import asyncio
import struct
import time
from collections.abc import Callable

import pytest

from app.core.config import Settings
from app.services.chat import ChatOrchestrator, PassthroughOutputGuard
from app.services.llm.base import ProviderTimeout
from app.services.llm.fake_provider import FakeLLMProvider
from app.services.nlp.keyword import KeywordFallbackAnalyzer
from app.services.nlp.redaction import Redactor
from app.services.safety.escalation import build_escalator
from app.services.safety.ml_classifier import NullClassifier
from app.services.safety.rules import build_engine
from app.services.voice.realtime import (
    CrisisAudioCache,
    LatencyBreakdown,
    PcmEnergyVad,
    RealtimeVoiceSession,
    SpeechSynthesizer,
    summarize_latency,
)
from tests.chat.conftest import FakeConversation

Factory = Callable[..., ChatOrchestrator]


@pytest.fixture
def make_voice_orchestrator() -> Factory:
    """A local copy keeps the voice simulation independent of chat test scope."""

    def build(
        llm: FakeLLMProvider | None = None, *, output_guard: PassthroughOutputGuard | None = None
    ) -> ChatOrchestrator:
        return ChatOrchestrator(
            llm=llm or FakeLLMProvider(),
            engine=build_engine(),
            classifier=NullClassifier(reason="disabled"),
            escalator=build_escalator(),
            analyzer=KeywordFallbackAnalyzer(),
            redactor=Redactor(),
            output_guard=output_guard,
            rate_limit_per_minute=1_000,
        )

    return build


class ScriptedTranscriber:
    def __init__(self, *, final: str, partial: str | None = None, fail: bool = False) -> None:
        self.final = final
        self.partial = partial
        self.fail = fail
        self.pushes = 0
        self.discarded = False

    async def push(self, pcm16le: bytes) -> str | None:
        del pcm16le
        self.pushes += 1
        if self.fail:
            from app.services.voice.stt import LocalTranscriptionError

            raise LocalTranscriptionError("test failure")
        return self.partial if self.pushes == 1 else None

    async def finish(self) -> str:
        if self.fail:
            from app.services.voice.stt import LocalTranscriptionError

            raise LocalTranscriptionError("test failure")
        return self.final

    def discard(self) -> None:
        self.discarded = True


class FakeSynthesizer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def synthesize(self, text: str, language: str) -> bytes:
        self.calls.append((text, language))
        return b"RIFF-test-wav"


def pcm(value: int, *, milliseconds: int = 100, sample_rate_hz: int = 16_000) -> bytes:
    """One deterministic mono PCM16 frame; no recorded or human audio fixture."""
    samples = sample_rate_hz * milliseconds // 1000
    return struct.pack(f"<{samples}h", *([value] * samples))


async def wait_for_event(events: list[dict[str, object]], event_type: str) -> dict[str, object]:
    for _ in range(100):
        for event in events:
            if event.get("type") == event_type:
                return event
        await asyncio.sleep(0.01)
    raise AssertionError(f"event {event_type!r} not received")


def voice_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "voice_vad_silence_ms": 100,
            "voice_stt_partial_interval_ms": 100,
            "voice_vad_energy_threshold": 500.0,
            "voice_max_utterance_seconds": 5.0,
        }
    )


def make_session(
    settings: Settings,
    orchestrator: ChatOrchestrator,
    conversation: FakeConversation,
    transcriber: ScriptedTranscriber,
    events: list[dict[str, object]],
    synthesizer: SpeechSynthesizer | None = None,
) -> RealtimeVoiceSession:
    tts = synthesizer or FakeSynthesizer()

    async def send(event: dict[str, object]) -> None:
        events.append(event)

    return RealtimeVoiceSession(
        settings=settings,
        orchestrator=orchestrator,
        conversation=conversation,
        locale="en",
        region="IN",
        send=send,
        transcriber_factory=lambda _locale, _rate: transcriber,
        synthesizer=tts,
        crisis_audio=CrisisAudioCache(tts),
    )


async def test_scripted_audio_streams_partial_stt_then_sentence_audio(
    settings: Settings, make_voice_orchestrator: Factory
) -> None:
    llm = FakeLLMProvider(chunks=["I hear you. ", "Let us take one small step."])
    orchestrator = make_voice_orchestrator(llm, output_guard=PassthroughOutputGuard())
    conversation = FakeConversation(stores=False)
    events: list[dict[str, object]] = []
    transcriber = ScriptedTranscriber(final="I need a small plan", partial="I need")
    session = make_session(
        voice_settings(settings), orchestrator, conversation, transcriber, events
    )

    await session.on_audio(pcm(2_000))
    await session.on_audio(pcm(0))
    await wait_for_event(events, "reply_final")

    user_events = [event for event in events if event.get("speaker") == "user"]
    assert user_events[0]["text"] == "I need"
    assert user_events[-1]["final"] is True
    assert user_events[-1]["text"] == "I need a small plan"
    audio_events = [event for event in events if event.get("type") == "assistant_audio"]
    assert len(audio_events) == 2, "each completed sentence is synthesized and sent promptly"
    final = await wait_for_event(events, "reply_final")
    latency = final["latency"]
    assert isinstance(latency, dict)
    assert latency["stt_first_partial_ms"] is not None
    assert latency["llm_first_token_ms"] is not None
    assert latency["time_to_first_audio_ms"] is not None
    assert len(llm.stream_calls) == 1, "voice uses the shared chat orchestrator stream"


async def test_barge_in_cancels_pending_assistant_audio_inside_target(
    settings: Settings, make_voice_orchestrator: Factory
) -> None:
    # The model waits before its first chunk, leaving a reliable window for the
    # next spoken PCM frame to interrupt it.
    llm = FakeLLMProvider(latency_ms=1_000, chunks=["This should not be spoken."])
    orchestrator = make_voice_orchestrator(llm, output_guard=PassthroughOutputGuard())
    events: list[dict[str, object]] = []
    first = ScriptedTranscriber(final="first voice turn")
    session = make_session(
        voice_settings(settings), orchestrator, FakeConversation(), first, events
    )

    await session.on_audio(pcm(2_000))
    await session.on_audio(pcm(0))
    await asyncio.sleep(0.02)
    assert session.assistant_speaking

    started = time.perf_counter()
    await session.on_audio(pcm(2_000))
    elapsed_ms = (time.perf_counter() - started) * 1000.0

    barge = await wait_for_event(events, "barge_in")
    assert elapsed_ms < settings.voice_barge_in_target_ms
    ack = barge["server_ack_ms"]
    assert isinstance(ack, float)
    assert ack < settings.voice_barge_in_target_ms
    await asyncio.sleep(0.02)
    assert not [event for event in events if event.get("type") == "assistant_audio"]
    await session.close()


async def test_high_risk_voice_transcript_uses_cached_crisis_audio_and_never_calls_llm(
    settings: Settings, make_voice_orchestrator: Factory
) -> None:
    llm = FakeLLMProvider(replies=["unsafe model words that must never be used"])
    orchestrator = make_voice_orchestrator(llm, output_guard=PassthroughOutputGuard())
    events: list[dict[str, object]] = []
    synth = FakeSynthesizer()
    session = make_session(
        voice_settings(settings),
        orchestrator,
        FakeConversation(stores=False),
        ScriptedTranscriber(final="I want to kill myself"),
        events,
        synth,
    )

    await session.on_audio(pcm(2_000))
    await session.on_audio(pcm(0))
    crisis = await wait_for_event(events, "crisis")

    assert len(llm.stream_calls) == 0
    assert crisis["continue_or_end"] is True
    metadata = crisis["metadata"]
    assert isinstance(metadata, dict)
    assert metadata["response_type"] == "crisis"
    assert metadata["resources"], "the on-screen crisis card has localised helplines"
    assert crisis["audio"], "reviewed local crisis audio is sent from the in-memory cache"


async def test_stt_failure_speaks_a_safe_type_instead_option(
    settings: Settings, make_voice_orchestrator: Factory
) -> None:
    events: list[dict[str, object]] = []
    session = make_session(
        voice_settings(settings),
        make_voice_orchestrator(output_guard=PassthroughOutputGuard()),
        FakeConversation(),
        ScriptedTranscriber(final="", fail=True),
        events,
    )

    await session.on_audio(pcm(2_000))
    fallback = await wait_for_event(events, "fallback")

    assert fallback["code"] == "stt_failure"
    assert "type" in str(fallback["message"]).lower()
    assert any(event.get("type") == "assistant_audio" for event in events)


async def test_llm_timeout_uses_the_orchestrator_fallback_and_speaks_it(
    settings: Settings, make_voice_orchestrator: Factory
) -> None:
    llm = FakeLLMProvider(error=ProviderTimeout("test timeout"))
    events: list[dict[str, object]] = []
    session = make_session(
        voice_settings(settings),
        make_voice_orchestrator(llm, output_guard=PassthroughOutputGuard()),
        FakeConversation(),
        ScriptedTranscriber(final="Could we talk?"),
        events,
    )

    await session.on_audio(pcm(2_000))
    await session.on_audio(pcm(0))
    fallback = await wait_for_event(events, "fallback")
    final = await wait_for_event(events, "reply_final")

    assert fallback["code"] == "llm_timeout"
    assert "limitation" in str(final["reply"])
    assert any(event.get("type") == "assistant_audio" for event in events)


def test_energy_vad_only_finds_speech_and_silence() -> None:
    vad = PcmEnergyVad(sample_rate_hz=16_000, energy_threshold=500.0, silence_ms=100)

    starts = vad.process(pcm(2_000))
    ends = vad.process(pcm(0))

    assert starts.speech_started is True
    assert starts.speech_ended is False
    assert ends.speech_ended is True
    assert set(starts.__dataclass_fields__) == {
        "speech_started",
        "speech_ended",
        "voiced",
        "duration_ms",
        "processing_ms",
    }


def test_latency_summary_reports_p50_and_p95() -> None:
    records: list[LatencyBreakdown] = []
    for offset in (1.0, 2.0, 9.0):
        record = LatencyBreakdown(
            vad_ms=offset,
            stt_first_partial_ms=10 + offset,
            stt_final_ms=20 + offset,
            llm_first_token_ms=30 + offset,
            tts_first_chunk_ms=40 + offset,
            first_audio_ms=50 + offset,
        )
        records.append(record)

    summary = summarize_latency(records)

    assert summary["time_to_first_audio_ms"] == {"p50": 52.0, "p95": 59.0}
    assert summary["vad_ms"] == {"p50": 2.0, "p95": 9.0}
