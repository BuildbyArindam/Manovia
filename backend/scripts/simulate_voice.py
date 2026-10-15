"""Offline scripted timing simulation for the Day 40 voice pipeline.

This is deliberately not a microphone benchmark. It feeds deterministic timing
fakes through the same stage recorder used by ``RealtimeVoiceSession`` so CI and
reviewers can see p50/p95 calculation without retaining recorded audio or
transcripts. Run from ``backend/``:

    ../.venv/bin/python scripts/simulate_voice.py
"""

from __future__ import annotations

import asyncio
import time

from app.services.voice.realtime import LatencyBreakdown, summarize_latency

SAMPLES = 25


async def scripted_turn(index: int) -> LatencyBreakdown:
    """One short warm-local turn with deterministic fake STT/LLM/TTS delays."""
    record = LatencyBreakdown()
    record.begin(time.perf_counter())

    vad_started = time.perf_counter()
    # Energy VAD has no network/model work. Yield once to model a WS hand-off.
    await asyncio.sleep(0)
    record.add_vad((time.perf_counter() - vad_started) * 1000.0)

    # Scripted local stages vary modestly, exercising p95 rather than printing
    # one repeated number. No fixture contains a real voice sample or words.
    await asyncio.sleep((22 + index % 4) / 1000.0)
    record.mark_partial(time.perf_counter())
    await asyncio.sleep((20 + index % 3) / 1000.0)
    record.mark_final_transcript(time.perf_counter())
    await asyncio.sleep((62 + index % 5) / 1000.0)
    record.mark_llm_token(time.perf_counter())
    tts_started = time.perf_counter()
    await asyncio.sleep((30 + index % 4) / 1000.0)
    record.mark_tts(tts_started, time.perf_counter())
    return record


async def main() -> int:
    records = [await scripted_turn(index) for index in range(SAMPLES)]
    summary = summarize_latency(records)
    print(f"Voice simulation: {SAMPLES} warm local scripted turns (all timings in ms)")
    print(f"{'stage':<28} {'p50':>9} {'p95':>9}")
    print("-" * 48)
    for stage in (
        "vad_ms",
        "stt_first_partial_ms",
        "stt_final_ms",
        "llm_first_token_ms",
        "tts_first_chunk_ms",
        "time_to_first_audio_ms",
    ):
        row = summary[stage]
        print(f"{stage:<28} {row['p50']:>9.3f} {row['p95']:>9.3f}")
    first_audio_p95 = summary["time_to_first_audio_ms"]["p95"]
    status = "PASS" if first_audio_p95 < 1500 else "FAIL"
    print(f"\n{status}: p95 time-to-first-audio {first_audio_p95:.3f} ms < 1500 ms target")
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
