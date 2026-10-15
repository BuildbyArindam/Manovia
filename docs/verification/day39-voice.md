# Verification: Day 39 Voice Journaling

## Automated Tests

1. **File deletion after STT:**
   `test_transcribe_audio_deleted` in `backend/tests/integration/test_voice.py` proves that temporal audio files uploaded to `/api/v1/voice/transcribe` are correctly transcribed and unlinked via `finally: os.unlink(temp_path)` before the HTTP response is sent.
   
2. **HIGH-risk phrase testing:**
   `test_voice_to_chat_crisis_path` verified that when the output of STT ("I want to end my life.") is sent to the chat composer, the backend pipeline deterministic safety patterns identify it as `HIGH` risk, bypass LLMs, and correctly trigger the crisis card (returning helpline numbers).

3. **Text-To-Speech Output:**
   `test_synthesize_voice` verifies `/api/v1/voice/synthesize` endpoint correctly binds and returns `audio/wav` formats without failing, invoking the local TTS logic.

4. **Accessibility (Playwright E2E):**
   `voice.spec.ts` keyboard navigates to the Voice Recorder button, asserts that `aria-hidden="true"` effectively masks pulsing UI elements from `axe-core`, and validates the absence of accessibility violations on the entire chat route.

## Manual Evaluation Checklist

Since sandbox networks restrict microphone/hardware access and STT/TTS Hugging Face downloads, the following tests should be conducted by the operator in their real deployment environment:

### 1. Transcribe 6 Sample Clips
- Plug in a microphone or configure virtual audio devices.
- Open the application, go to `/chat`, and click `🎤 Voice Journal`.
- Record 6 sample clips. Note transcription latency and accuracy on screen.

### 2. Privacy Grep (Audio Bytes)
- Start the backend via `make dev`.
- Send an audio recording.
- Run `grep -ri "audio" /tmp/api.log` (or your server logs) to guarantee that binary audio contents/payloads are NEVER written out to application logs.

### 3. Word Error Rate (WER) Evaluation
To compute WER across a 30-utterance set (as per brief):
1. Prepare `evals/datasets/voice_30_utterances.jsonl` with lines like `{"audio_path": "clip1.wav", "reference_text": "hello"}`.
2. Install dependencies: `pip install jiwer faster_whisper`.
3. Run `python backend/scripts/evaluate_stt.py --dataset evals/datasets/voice_30_utterances.jsonl`.
4. Document the WER results and latency limits observed on CPU vs. GPU.
