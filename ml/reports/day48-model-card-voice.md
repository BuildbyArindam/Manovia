# Model card — Voice STT/VAD/TTS (Day 48)

| | |
| --- | --- |
| Card version | 1.0 (2026-10-20) |
| Status | candidate (offline fake tested; real STT/TTS not measured in sandbox) |
| Run id | n/a — rule-based + provider adapters |
| Config | `backend/app/core/config.py` voice_* |
| Git commit | 5b67225 (base) + Day-48 work |
| Seed | n/a |
| Owner | Manovia maintainers |

## 1. What it is

WebRTC VAD (mode 2) for speech/silence over 10 ms PCM frames (in-memory only) + STT + LLM + TTS pipeline. The voice transcript goes through ChatOrchestrator.admit()/stream() — no voice-only LLM route.

## 2. Intended use

| | |
| --- | --- |
| Primary use | Turn spoken input into a safety-checked reply, spoken back sentence-buffered |
| Users | End user via browser microphone |
| Out of scope | Emotion/health/identity inference; the code has none and a grep proves it |
| Failure mode | At HIGH/IMMINENT, pre-warmed crisis audio + card, LLM call count=0 |

## 3. Training data

STT: optional local Piper/faster-whisper — not trained by us, not evaluated here. VAD: WebRTC model is pre-trained; we configure but do not train.

## 4. Evaluation

Voice-sim: 25 fake turns, p95 time-to-first-audio 98.6 ms (target 1500 ms). Barge-in 0.165 ms (target 300 ms). Real-mic, real-network, real-STT not run in sandbox.

## 5. Known limitations

- No mic/Bluetooth in sandbox; real latency and device routing untested.
- Speech synthesis in tests is fake WAV silence.
- STT WER/latency unmeasured; local model download needs network outside sandbox.

## 6. Fairness

Voice inherits text fairness gaps — paired-sentence audit covers text path only. Acoustic fairness (accent, gender pitch) not measured; VAD is language-agnostic only for energy, not content.

## 7. Safety relationship

| | |
| --- | --- |
| Can it lower a safety level? | **No** — transcript goes through same crisis gate as typed |
| Can it see identifiers? | No |
| Called at HIGH/IMMINENT? | No — crisis audio short-circuits LLM |
| What checks output? | Day-14 output guard over the final text before TTS |
| Kill switch | `voice=false` hides mic, fallback is typed chat |

## 8. Decision

Ship behind `voice_realtime_enabled` + runtime flag `voice`. Revisit after real-device checklist (docs/verification/day40).
