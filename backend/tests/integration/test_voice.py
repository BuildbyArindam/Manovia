import os
import tempfile
from typing import Any

import pytest
from httpx import AsyncClient
from pytest import MonkeyPatch


@pytest.fixture
def mock_stt(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("VOICE_STT_FAKE", "true")
    monkeypatch.setenv("APP_ENV", "test")


async def get_auth_headers(client: AsyncClient) -> dict[str, str]:
    response = await client.post("/api/v1/auth/guest")
    assert response.status_code == 201
    token = response.json()["access_token"]

    headers = {"Authorization": f"Bearer {token}"}

    # Consent
    await client.post(
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
    return headers


@pytest.mark.asyncio
async def test_transcribe_audio_deleted(client: AsyncClient, mock_stt: None) -> None:
    headers = await get_auth_headers(client)

    audio_content = b"fake audio content"
    files = {"audio": ("test.wav", audio_content, "audio/wav")}

    response = await client.post("/api/v1/voice/transcribe", files=files, headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert "text" in data
    assert data["text"] == "This is a fake transcription of short audio."

    # Ensure there are no leftover temp files
    temp_dir = tempfile.gettempdir()
    leftovers = [f for f in os.listdir(temp_dir) if "test.wav" in f]
    assert len(leftovers) == 0


@pytest.mark.asyncio
async def test_tts_available_voices(client: AsyncClient) -> None:
    headers = await get_auth_headers(client)
    response = await client.get("/api/v1/voice/voices", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert "voices" in data
    langs = {v["language"] for v in data["voices"]}
    assert "en" in langs
    assert "hi" in langs
    assert "bn" in langs


@pytest.mark.asyncio
async def test_voice_to_chat_crisis_path(
    client: AsyncClient, mock_stt: None, monkeypatch: MonkeyPatch
) -> None:
    headers = await get_auth_headers(client)

    # Send to chat
    # Create a session first
    res_session = await client.post("/api/v1/chat/sessions", headers=headers)
    assert res_session.status_code == 201
    session_id = res_session.json()["id"]

    transcript = "I want to end my life."

    res_chat = await client.post(
        f"/api/v1/chat/sessions/{session_id}/messages",
        headers=headers,
        json={"message": transcript},
    )
    assert res_chat.status_code == 200
    chat_data = res_chat.json()
    assert chat_data["metadata"]["risk_level"] == "high"
    assert "helpline" in chat_data["reply"].lower() or "crisis" in chat_data["reply"].lower()


@pytest.mark.asyncio
async def test_synthesize_voice(client: AsyncClient) -> None:
    headers = await get_auth_headers(client)
    data = {"text": "hello world", "language": "en", "speed": 1.0}
    response = await client.post("/api/v1/voice/synthesize", data=data, headers=headers)
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"


@pytest.mark.asyncio
async def test_transcribe_audio_real(client: AsyncClient, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("VOICE_STT_FAKE", "false")

    class FakeWhisperModel:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def transcribe(self, audio: str, beam_size: int = 5) -> tuple[list[Any], Any]:
            class FakeSegment:
                text = "Real transcription."

            class FakeInfo:
                language = "en"
                language_probability = 1.0

            return [FakeSegment()], FakeInfo()

    import sys

    class FakeModule:
        WhisperModel = FakeWhisperModel

    monkeypatch.setitem(sys.modules, "faster_whisper", FakeModule)
    headers = await get_auth_headers(client)
    audio_content = b"fake"
    files = {"audio": ("test.wav", audio_content, "audio/wav")}
    response = await client.post("/api/v1/voice/transcribe", files=files, headers=headers)
    assert response.status_code == 200
    assert response.json()["text"] == "Real transcription."
