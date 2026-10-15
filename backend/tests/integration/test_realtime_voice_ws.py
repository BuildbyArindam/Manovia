"""WebSocket integration: auth, streaming PCM, and crisis safety share chat's path."""

from __future__ import annotations

import base64
import struct

from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import AsyncClient

from app.services.llm.fake_provider import FakeLLMProvider
from app.services.voice.realtime import CrisisAudioCache, VoiceRuntime


class SocketTranscriber:
    async def push(self, pcm16le: bytes) -> str | None:
        del pcm16le
        return None

    async def finish(self) -> str:
        return "I want to kill myself"

    def discard(self) -> None:
        return None


class SocketSynthesizer:
    def synthesize(self, text: str, language: str) -> bytes:
        del text, language
        return b"RIFF-test-wav"


def pcm_b64() -> str:
    return base64.b64encode(struct.pack("<1600h", *([2_000] * 1600))).decode("ascii")


async def auth_headers(client: AsyncClient) -> dict[str, str]:
    guest = await client.post("/api/v1/auth/guest")
    assert guest.status_code == 201
    token = guest.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    consent = await client.post(
        "/api/v1/consent",
        headers=headers,
        json={
            "grants": [
                {"kind": "terms", "version": "2026-10-01", "granted": True},
                {"kind": "privacy", "version": "2026-10-01", "granted": True},
                {"kind": "ai_disclosure", "version": "2026-10-01", "granted": True},
            ]
        },
    )
    assert consent.status_code == 201
    return headers


async def test_realtime_socket_crisis_stops_before_llm_and_returns_crisis_event(
    app: FastAPI, client: AsyncClient
) -> None:
    headers = await auth_headers(client)
    session = await client.post("/api/v1/chat/sessions", headers=headers)
    assert session.status_code == 201
    session_id = session.json()["id"]

    fake_llm = FakeLLMProvider(replies=["this must never run"])
    from app.services.chat import build_orchestrator

    orchestrator = build_orchestrator(app.state.settings, llm=fake_llm)
    app.state.chat_orchestrator = orchestrator
    synthesizer = SocketSynthesizer()
    app.state.voice_runtime = VoiceRuntime(
        settings=app.state.settings,
        transcriber_factory=lambda _locale, _rate: SocketTranscriber(),
        synthesizer=synthesizer,
        crisis_audio=CrisisAudioCache(synthesizer),
    )

    token = headers["Authorization"].removeprefix("Bearer ")
    with (
        TestClient(app) as test_client,
        test_client.websocket_connect(
            f"/api/v1/voice/sessions/{session_id}/stream",
            subprotocols=["manovia.v1", f"bearer.{token}"],
        ) as socket,
    ):
        assert socket.accepted_subprotocol == "manovia.v1"
        ready = socket.receive_json()
        assert ready["type"] == "ready"
        socket.send_json({"type": "start", "locale": "en", "region": "IN"})
        assert socket.receive_json()["type"] == "started"
        socket.send_json({"type": "audio", "pcm": pcm_b64()})
        silence = base64.b64encode(b"\0" * 3_200).decode("ascii")
        # WebRTC has a bounded 100 ms voiced-frame hangover, then the default
        # VAD closes after its 500 ms silence window.
        for _ in range(6):
            socket.send_json({"type": "audio", "pcm": silence})

        events: list[dict[str, object]] = []
        for _ in range(12):
            event = socket.receive_json()
            events.append(event)
            if event["type"] == "crisis":
                break

    crisis = next(event for event in events if event["type"] == "crisis")
    metadata = crisis["metadata"]
    assert isinstance(metadata, dict)
    assert metadata["response_type"] == "crisis"
    assert metadata["resources"]
    assert not [event for event in events if event["type"] == "assistant_audio"]
    assert fake_llm.call_count == 0
    assert len(fake_llm.stream_calls) == 0
