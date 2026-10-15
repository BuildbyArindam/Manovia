"""Real-time voice companion primitives.

The WebSocket route is intentionally a thin transport around this module.  This
keeps the safety property visible and testable: decoded words always enter
``ChatOrchestrator.admit()`` / ``ChatOrchestrator.stream()``.  There is no
voice-only LLM route and no audio-derived emotion, health, identity, or affect
inference. The only signal operation is short-lived WebRTC VAD used to tell
speech from silence so the recogniser knows when to listen. It is used only for
that boundary decision and emits no emotion, health, identity, or speaker
inference.

All audio bytes, partial transcripts, and assistant text stay in memory for the
active socket.  They are never written to a database, temporary file, cache, or
log.  The one exception is deterministic, reviewed crisis *assistant* audio in
``CrisisAudioCache``; it contains no user content and is held only in process
memory so it can be played immediately during a crisis response.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import math
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Protocol

import webrtcvad  # type: ignore[import-untyped]

from app.content.i18n import RenderedTemplate
from app.core.config import Settings
from app.services.chat import ChatOrchestrator, Conversation, FinalEvent, TokenEvent
from app.services.chat.types import ChatMetadata, ResponseType
from app.services.safety.base import AssessmentContext, RiskAssessment, RiskLevel
from app.services.safety.escalation import build_escalator
from app.services.voice.stt import (
    LocalTranscriptionError,
    SpeechSynthesisError,
    build_incremental_transcriber_factory,
    generate_speech,
)

# These are reviewed fallback words, used only when an otherwise unavailable
# stage prevents a voice reply.  They never quote or infer anything from audio.
STT_FAILURE_MESSAGE = "I couldn't understand that audio. You can type instead if that is easier."
NETWORK_FAILURE_MESSAGE = "The connection dropped. You can type instead if that is easier."
SILENCE_TIMEOUT_MESSAGE = (
    "I haven't heard anything for a while, so this voice session has paused. You can type instead."
)


class VoiceProtocolError(ValueError):
    """A malformed or out-of-policy wire message.  It carries no user content."""


class TranscriptionError(RuntimeError):
    """The local recogniser could not produce a transcript.  Never carries audio/text."""


class SpeechSynthesizer(Protocol):
    """Local TTS interface.  Implementations receive no identifiers or metadata."""

    def synthesize(self, text: str, language: str) -> bytes:
        """Return a complete WAV payload for one reviewed assistant sentence."""


class IncrementalTranscriber(Protocol):
    """In-memory incremental STT interface, swappable for fakes in tests."""

    async def push(self, pcm16le: bytes) -> str | None:
        """Accept one voiced PCM chunk and optionally return a provisional transcript."""

    async def finish(self) -> str:
        """Return the final transcript for the current utterance."""

    def discard(self) -> None:
        """Erase all buffered audio and transcript state immediately."""


TranscriberFactory = Callable[[str, int], IncrementalTranscriber]
SendEvent = Callable[[dict[str, object]], Awaitable[None]]


@dataclass(frozen=True)
class VadResult:
    """Speech boundaries found in one PCM chunk; no emotional/health features exist here."""

    speech_started: bool
    speech_ended: bool
    voiced: bool
    duration_ms: float
    processing_ms: float


class WebRtcVad:
    """Short-window WebRTC VAD for signed 16-bit mono PCM.

    WebRTC VAD answers only whether a 10 ms speech frame is voiced. Its boolean
    is used to start/end an utterance; no score, embedding, identity, emotion,
    affect, or health signal is exposed or retained. A partially filled final
    frame is intentionally discarded for the gate (the original PCM still goes
    to STT) so arbitrary Web Audio callback sizes remain supported.
    """

    _FRAME_MS = 10

    def __init__(self, *, sample_rate_hz: int, mode: int, silence_ms: int) -> None:
        self._sample_rate_hz = sample_rate_hz
        self._frame_bytes = sample_rate_hz * 2 * self._FRAME_MS // 1000
        self._vad = webrtcvad.Vad(mode)
        self._silence_ms_limit = silence_ms
        self._speaking = False
        self._silence_ms = 0.0

    @property
    def speaking(self) -> bool:
        return self._speaking

    def process(self, pcm16le: bytes) -> VadResult:
        if not pcm16le or len(pcm16le) % 2:
            raise VoiceProtocolError("PCM chunks must be non-empty signed 16-bit mono samples")
        started = time.perf_counter()
        sample_count = len(pcm16le) // 2
        duration_ms = sample_count * 1000.0 / self._sample_rate_hz
        # Evaluate *every* complete 10 ms frame. `any(generator)` would stop at
        # the first voiced frame and leave WebRTC's internal hangover state six
        # frames behind a 100 ms packet, delaying an end-of-speech boundary.
        frame_voiced = [
            self._vad.is_speech(pcm16le[offset : offset + self._frame_bytes], self._sample_rate_hz)
            for offset in range(0, len(pcm16le) - self._frame_bytes + 1, self._frame_bytes)
        ]
        voiced = any(frame_voiced)
        speech_started = False
        speech_ended = False

        if voiced:
            self._silence_ms = 0.0
            if not self._speaking:
                self._speaking = True
                speech_started = True
        elif self._speaking:
            self._silence_ms += duration_ms
            if self._silence_ms >= self._silence_ms_limit:
                self._speaking = False
                self._silence_ms = 0.0
                speech_ended = True

        return VadResult(
            speech_started=speech_started,
            speech_ended=speech_ended,
            voiced=voiced,
            duration_ms=duration_ms,
            processing_ms=(time.perf_counter() - started) * 1000.0,
        )


@dataclass
class LatencyBreakdown:
    """Per-utterance timings sent only to the connected client.

    Values are wall-clock measurements, not estimates.  The class never holds
    words or samples and is intentionally not persisted/logged.
    """

    utterance_started_at: float | None = None
    transcript_final_at: float | None = None
    vad_ms: float = 0.0
    stt_first_partial_ms: float | None = None
    stt_final_ms: float | None = None
    llm_first_token_ms: float | None = None
    tts_first_chunk_ms: float | None = None
    first_audio_ms: float | None = None

    def begin(self, now: float) -> None:
        self.utterance_started_at = now
        self.transcript_final_at = None
        self.vad_ms = 0.0
        self.stt_first_partial_ms = None
        self.stt_final_ms = None
        self.llm_first_token_ms = None
        self.tts_first_chunk_ms = None
        self.first_audio_ms = None

    def add_vad(self, milliseconds: float) -> None:
        self.vad_ms += milliseconds

    def mark_partial(self, now: float) -> None:
        if self.utterance_started_at is not None and self.stt_first_partial_ms is None:
            self.stt_first_partial_ms = (now - self.utterance_started_at) * 1000.0

    def mark_final_transcript(self, now: float) -> None:
        if self.utterance_started_at is None:
            return
        self.transcript_final_at = now
        self.stt_final_ms = (now - self.utterance_started_at) * 1000.0

    def mark_llm_token(self, now: float) -> None:
        if self.transcript_final_at is not None and self.llm_first_token_ms is None:
            self.llm_first_token_ms = (now - self.transcript_final_at) * 1000.0

    def mark_tts(self, started: float, finished: float) -> None:
        if self.tts_first_chunk_ms is None:
            self.tts_first_chunk_ms = (finished - started) * 1000.0
        if self.transcript_final_at is not None and self.first_audio_ms is None:
            self.first_audio_ms = (finished - self.transcript_final_at) * 1000.0

    def event_payload(self) -> dict[str, float | None]:
        return {
            "vad_ms": round(self.vad_ms, 3),
            "stt_first_partial_ms": _rounded(self.stt_first_partial_ms),
            "stt_final_ms": _rounded(self.stt_final_ms),
            "llm_first_token_ms": _rounded(self.llm_first_token_ms),
            "tts_first_chunk_ms": _rounded(self.tts_first_chunk_ms),
            "time_to_first_audio_ms": _rounded(self.first_audio_ms),
        }


def _rounded(value: float | None) -> float | None:
    return round(value, 3) if value is not None else None


def percentile(samples: Sequence[float], percentage: int) -> float:
    """Nearest-rank percentile for the small deterministic simulation sample."""
    if not samples:
        raise ValueError("percentile needs at least one sample")
    if not 0 < percentage <= 100:
        raise ValueError("percentage must be in (0, 100]")
    ordered = sorted(samples)
    index = max(0, math.ceil(len(ordered) * percentage / 100) - 1)
    return ordered[index]


def summarize_latency(records: Sequence[LatencyBreakdown]) -> dict[str, dict[str, float]]:
    """Return p50/p95 only for stages observed by every supplied record."""
    keys = (
        "vad_ms",
        "stt_first_partial_ms",
        "stt_final_ms",
        "llm_first_token_ms",
        "tts_first_chunk_ms",
        "time_to_first_audio_ms",
    )
    result: dict[str, dict[str, float]] = {}
    for key in keys:
        values: list[float] = []
        for record in records:
            raw = record.event_payload()[key]
            if raw is not None:
                values.append(float(raw))
        if values:
            result[key] = {"p50": percentile(values, 50), "p95": percentile(values, 95)}
    return result


class SentenceBuffer:
    """Hold token fragments until a sentence can be spoken naturally."""

    def __init__(self) -> None:
        self._buffer = ""

    def push(self, token: str) -> list[str]:
        self._buffer += token
        finished: list[str] = []
        start = 0
        for index, character in enumerate(self._buffer):
            if character in ".!?।" and (
                index + 1 == len(self._buffer) or self._buffer[index + 1].isspace()
            ):
                sentence = self._buffer[start : index + 1].strip()
                if sentence:
                    finished.append(sentence)
                start = index + 1
        self._buffer = self._buffer[start:]
        return finished

    def flush(self) -> list[str]:
        sentence = self._buffer.strip()
        self._buffer = ""
        return [sentence] if sentence else []


class LocalSpeechSynthesizer:
    """TTS adapter around the existing local-only speech implementation."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def synthesize(self, text: str, language: str) -> bytes:
        return generate_speech(
            text,
            language,
            provider=self._settings.voice_tts_provider,
            piper_model_paths={
                "en": self._settings.voice_piper_model_en,
                "hi": self._settings.voice_piper_model_hi,
                "bn": self._settings.voice_piper_model_bn,
            },
        )


@dataclass
class VoiceRuntime:
    """Process-wide voice collaborators; no participant data lives here."""

    settings: Settings
    transcriber_factory: TranscriberFactory
    synthesizer: SpeechSynthesizer
    crisis_audio: CrisisAudioCache

    @classmethod
    def build(cls, settings: Settings) -> VoiceRuntime:
        synthesizer = LocalSpeechSynthesizer(settings)
        return cls(
            settings=settings,
            transcriber_factory=build_incremental_transcriber_factory(settings),
            synthesizer=synthesizer,
            crisis_audio=CrisisAudioCache(synthesizer),
        )

    async def prewarm_crisis_audio(self, *, locale: str, region: str | None) -> None:
        """Prepare reviewed high-risk responses before a person starts speaking."""
        escalator = build_escalator()
        plans = (
            escalator.plan(RiskAssessment(level=RiskLevel.HIGH), locale=locale, region=region),
            escalator.plan(RiskAssessment(level=RiskLevel.IMMINENT), locale=locale, region=region),
            escalator.plan(
                RiskAssessment(
                    level=RiskLevel.HIGH,
                    context=AssessmentContext(third_person=True, fiction_frame=True),
                ),
                locale=locale,
                region=region,
            ),
        )
        messages = [plan.message for plan in plans if plan.message is not None]
        await self.crisis_audio.prewarm(messages)


@dataclass
class CrisisAudioCache:
    """Local in-memory cache of deterministic, reviewed crisis assistant audio."""

    synthesizer: SpeechSynthesizer
    _cache: dict[tuple[str, str], bytes] = field(default_factory=dict)

    async def get(self, message: RenderedTemplate) -> bytes:
        text = spoken_crisis_text(message)
        key = (message.locale, text)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        # This text comes exclusively from reviewed localised content, never
        # from the person speaking.  Keep it in process memory, not on disk.
        audio = await asyncio.to_thread(self.synthesizer.synthesize, text, message.locale)
        self._cache[key] = audio
        return audio

    async def prewarm(self, messages: Sequence[RenderedTemplate]) -> None:
        for message in messages:
            try:
                await self.get(message)
            except SpeechSynthesisError:
                # A missing optional local Piper model is surfaced when the
                # session needs audio; it must not crash app startup.
                continue


def spoken_crisis_text(message: RenderedTemplate) -> str:
    """The short, deterministic spoken form of the on-screen crisis template."""
    parts = [message.title, *message.body]
    for item in (message.emergency_instruction, message.trusted_person, message.closing):
        if item:
            parts.append(item)
    return " ".join(parts)


class RealtimeVoiceSession:
    """One in-memory voice session, driven by a WebSocket route or a test fake.

    ``on_audio`` is deliberately the only audio entrypoint.  It processes an
    energy gate, hands voiced chunks to incremental STT, and routes completed
    words into the existing chat orchestrator.  Calling ``end`` or ``close``
    clears the recogniser buffer immediately.
    """

    def __init__(
        self,
        *,
        settings: Settings,
        orchestrator: ChatOrchestrator,
        conversation: Conversation,
        locale: str,
        region: str | None,
        send: SendEvent,
        transcriber_factory: TranscriberFactory,
        synthesizer: SpeechSynthesizer,
        crisis_audio: CrisisAudioCache,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._settings = settings
        self._orchestrator = orchestrator
        self._conversation = conversation
        self._locale = locale
        self._region = region
        self._send = send
        self._transcriber_factory = transcriber_factory
        self._synthesizer = synthesizer
        self._crisis_audio = crisis_audio
        self._clock = clock
        self._vad = WebRtcVad(
            sample_rate_hz=settings.voice_sample_rate_hz,
            mode=settings.voice_vad_mode,
            silence_ms=settings.voice_vad_silence_ms,
        )
        self._transcriber: IncrementalTranscriber | None = None
        self._latency = LatencyBreakdown()
        self._utterance_audio_ms = 0.0
        self._answer_task: asyncio.Task[None] | None = None
        self._generation = 0
        self._closed = False
        self._send_lock = asyncio.Lock()

    @property
    def latency(self) -> LatencyBreakdown:
        return self._latency

    @property
    def assistant_speaking(self) -> bool:
        return self._answer_task is not None and not self._answer_task.done()

    async def on_audio(self, pcm16le: bytes) -> None:
        """Process one transient PCM chunk; audio never reaches disk or a log."""
        if self._closed:
            return
        if len(pcm16le) > self._settings.voice_max_chunk_bytes:
            raise VoiceProtocolError("audio chunk exceeds the configured byte limit")
        result = self._vad.process(pcm16le)
        self._latency.add_vad(result.processing_ms)

        # The server cancellation is a belt-and-braces complement to the
        # browser's synchronous ``AudioBufferSourceNode.stop()``.  It prevents
        # new chunks from being sent after a barge-in acknowledgement.
        if result.speech_started:
            if self.assistant_speaking:
                await self._barge_in()
            self._latency.begin(self._clock())
            self._utterance_audio_ms = 0.0
            self._transcriber = self._transcriber_factory(
                self._locale, self._settings.voice_sample_rate_hz
            )
            await self._emit({"type": "speech_started"})

        if result.voiced and self._transcriber is not None:
            self._utterance_audio_ms += result.duration_ms
            if self._utterance_audio_ms > self._settings.voice_max_utterance_seconds * 1000.0:
                await self._stt_failure("utterance_too_long")
                return
            try:
                partial = await self._transcriber.push(pcm16le)
            except (TranscriptionError, LocalTranscriptionError):
                await self._stt_failure("stt_failure")
                return
            if partial:
                self._latency.mark_partial(self._clock())
                await self._emit(
                    {"type": "transcript", "speaker": "user", "text": partial, "final": False}
                )

        if result.speech_ended and self._transcriber is not None:
            await self._finish_utterance()

    async def finish_now(self) -> None:
        """Finish an utterance after an explicit client stop, if one is active."""
        if self._transcriber is not None:
            await self._finish_utterance()

    async def close(self) -> None:
        """Cancel output and wipe all short-lived voice buffers."""
        self._closed = True
        self._generation += 1
        if self._answer_task is not None:
            self._answer_task.cancel()
            await _await_cancelled(self._answer_task)
        self._answer_task = None
        if self._transcriber is not None:
            self._transcriber.discard()
        self._transcriber = None

    async def _barge_in(self) -> None:
        started = self._clock()
        self._generation += 1
        if self._answer_task is not None:
            self._answer_task.cancel()
        # Sending this event before waiting for a possibly slow TTS thread is
        # what lets the browser stop presently scheduled audio in <300 ms.
        await self._emit(
            {
                "type": "barge_in",
                "stop_audio": True,
                "server_ack_ms": round((self._clock() - started) * 1000.0, 3),
                "target_ms": self._settings.voice_barge_in_target_ms,
            }
        )

    async def _finish_utterance(self) -> None:
        transcriber = self._transcriber
        self._transcriber = None
        if transcriber is None:
            return
        try:
            text = (await transcriber.finish()).strip()
        except (TranscriptionError, LocalTranscriptionError):
            await self._stt_failure("stt_failure")
            return
        finally:
            transcriber.discard()
        if not text:
            await self._stt_failure("stt_empty")
            return
        self._latency.mark_final_transcript(self._clock())
        await self._emit({"type": "transcript", "speaker": "user", "text": text, "final": True})
        generation = self._generation
        self._answer_task = asyncio.create_task(self._answer(text, generation))

    async def _answer(self, transcript: str, generation: int) -> None:
        """Run the one safe chat orchestrator and stream sentences to local TTS."""
        try:
            admitted = self._orchestrator.admit(
                user_id=self._conversation.user_id,
                text=transcript,
                region=self._region,
                locale=self._locale,
            )
            sentences = SentenceBuffer()
            first_token = True
            async for event in self._orchestrator.stream(admitted, self._conversation):
                if self._closed or generation != self._generation:
                    return
                if isinstance(event, TokenEvent):
                    if first_token:
                        self._latency.mark_llm_token(self._clock())
                        first_token = False
                    await self._emit(
                        {
                            "type": "transcript",
                            "speaker": "assistant",
                            "text": event.text,
                            "final": False,
                        }
                    )
                    # A deterministic crisis response is one token followed by
                    # final metadata. Keep its text visible, but do not begin
                    # ordinary sentence TTS: the final branch sends the
                    # already-cached, localised crisis audio instead.
                    if not event.crisis:
                        for sentence in sentences.push(event.text):
                            await self._speak_sentence(sentence, generation)
                    continue
                if isinstance(event, FinalEvent):
                    await self._finish_reply(
                        event.reply.metadata, event.reply.reply, sentences, generation
                    )
                    return
        except asyncio.CancelledError:
            raise
        except Exception:
            # The orchestrator normally returns a safe canned fallback for an
            # LLM outage.  This last-resort transport error is deliberately
            # generic and offers typing; it never echoes the transcript.
            await self._speak_fallback("voice_error", NETWORK_FAILURE_MESSAGE, generation)

    async def _finish_reply(
        self,
        metadata: ChatMetadata,
        reply: str,
        sentences: SentenceBuffer,
        generation: int,
    ) -> None:
        if metadata.response_type is ResponseType.CRISIS and metadata.crisis is not None:
            # Deterministic, localised and cached before/through the session.
            # It comes from the same orchestrator final metadata, so LLM use is
            # still exactly zero for a high-risk transcript.
            audio = await self._crisis_audio.get(metadata.crisis)
            if self._closed or generation != self._generation:
                return
            await self._emit(
                {
                    "type": "crisis",
                    "metadata": metadata.model_dump(mode="json"),
                    "audio": _b64(audio),
                    "mime_type": "audio/wav",
                    "continue_or_end": True,
                }
            )
        else:
            for sentence in sentences.flush():
                await self._speak_sentence(sentence, generation)
            if metadata.response_type is ResponseType.FALLBACK or metadata.degraded:
                await self._emit({"type": "fallback", "code": "llm_timeout", "message": reply})
        await self._emit(
            {
                "type": "reply_final",
                "reply": reply,
                "metadata": metadata.model_dump(mode="json"),
                "latency": self._latency.event_payload(),
            }
        )

    async def _speak_sentence(self, sentence: str, generation: int) -> None:
        started = self._clock()
        try:
            audio = await asyncio.to_thread(self._synthesizer.synthesize, sentence, self._locale)
        except SpeechSynthesisError:
            # Text is still visible, and the client receives an explicit option
            # to type.  No remote TTS fallback is permitted.
            await self._emit(
                {
                    "type": "fallback",
                    "code": "tts_failure",
                    "message": "Audio is unavailable. You can keep talking or type instead.",
                }
            )
            return
        finished = self._clock()
        if self._closed or generation != self._generation:
            return
        self._latency.mark_tts(started, finished)
        await self._emit(
            {
                "type": "assistant_audio",
                "audio": _b64(audio),
                "mime_type": "audio/wav",
                "text": sentence,
            }
        )

    async def _stt_failure(self, code: str) -> None:
        if self._transcriber is not None:
            self._transcriber.discard()
        self._transcriber = None
        await self._speak_fallback(code, STT_FAILURE_MESSAGE, self._generation)

    async def _speak_fallback(self, code: str, message: str, generation: int) -> None:
        if self._closed or generation != self._generation:
            return
        await self._emit({"type": "fallback", "code": code, "message": message})
        await self._emit(
            {"type": "transcript", "speaker": "assistant", "text": message, "final": True}
        )
        await self._speak_sentence(message, generation)

    async def _emit(self, event: dict[str, object]) -> None:
        async with self._send_lock:
            await self._send(event)


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def decode_pcm_chunk(encoded: object, *, max_bytes: int) -> bytes:
    """Strictly decode one base64 PCM chunk without retaining the encoded form."""
    if not isinstance(encoded, str) or not encoded:
        raise VoiceProtocolError("audio chunks need a non-empty base64 pcm field")
    try:
        pcm = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise VoiceProtocolError("audio chunks must be valid base64") from exc
    if not pcm or len(pcm) > max_bytes or len(pcm) % 2:
        raise VoiceProtocolError("audio chunk size is invalid")
    return pcm


async def _await_cancelled(task: asyncio.Task[None]) -> None:
    try:
        await task
    except asyncio.CancelledError:
        return
