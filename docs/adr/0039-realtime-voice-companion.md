# ADR 0039 — Real-time voice companion: same-origin WebSocket, WebRTC VAD and crisis-first streaming

Date: 2026-10-15. Status: accepted.

## Context

Day 39's recorder transcribed a completed recording into the typed composer. It
could not support a conversational turn, incremental assistant speech, a prompt
barge-in, or an interruption-safe crisis response. The new path must keep the
existing chat safety order intact: only words from speech recognition may enter
the text safety/orchestration path, and high-risk text must never reach an LLM.

Browsers cannot attach the regular `Authorization` header when establishing a
WebSocket. The product also has a strict privacy default: raw microphone audio
and live transcript must not be written to a database, temporary file, browser
storage, cache, or application log merely to operate a live session.

## Decision

- Use an authenticated **same-origin WebSocket** rather than WebRTC for this
  application-to-server stream. The browser sends short mono PCM16 frames as
  base64 JSON messages and receives transcript, audio, crisis and state events
  on the same socket. The deployment proxy forwards WebSocket upgrades and
  disables response buffering on `/api`.
- Authenticate with two offered subprotocols: public `manovia.v1` and
  `bearer.<short-lived-access-token>`. The server validates the access token,
  refresh-token family, user, consent and chat-session ownership, then accepts
  only `manovia.v1`. The credential is never put in a URL/query string or
  reflected as the accepted subprotocol.
- Run **WebRTC VAD** over ephemeral 10 ms PCM16 frames. It produces only a
  speech/silence boundary; no voice feature, score or model output is available
  for emotion, mental/health, identity or speaker inference. Incremental STT
  emits words only, and a final transcript is admitted to the existing
  `ChatOrchestrator.admit()` then `stream()` path.
- Buffer LLM output to sentence boundaries and synthesize each completed
  sentence locally, then emit WAV chunks for immediate browser Web Audio
  playback. Browser-local signal detection is a boolean transport gate only; it
  stops scheduled playback synchronously on the first new speech frame while
  the server cancels pending generation as a second layer.
- Prewarm and cache only reviewed, deterministic, localised **assistant** crisis
  WAVs in process memory. A crisis event exposes visible localised helplines and
  continue/end choices. It does not synthesize generic generated text first;
  high/imminent transcript tests pin LLM calls and streams at zero.
- Keep all live PCM, transcript rows, playback queue and latency display in
  process/React/Web Audio memory for the open session. The existing chat-history
  choice remains the only explicit route by which the user can elect to save a
  chat. A browser-native spoken fallback covers a dropped connection; STT and
  LLM failures receive server-synthesized type-instead speech.

## Consequences

This keeps one auditable crisis-first safety pipeline rather than creating a
voice-only LLM bypass. The handshake is secure enough for browser WebSockets
without leaking credentials into URLs, but it requires proxy upgrade support and
same-origin microphone permission. The component retains a typed-chat fallback
because microphone permission, local models, audio devices and network links can
fail.

WebRTC VAD is a speech-boundary classifier, not a clinical or affect-analysis
system. Its short built-in hangover is intentionally accounted for by consuming
every 10 ms frame before the configured silence interval begins. It does not
change what text safety considers; it only decides when STT is asked to finish.

This decision does not provide real-device microphone verification, Bluetooth
routing verification, a browser WebRTC peer connection, cloud STT/TTS fallback,
or automatic retention of voice content. Those would require separate privacy,
security and clinical review.

## Verification

- `backend/tests/voice/test_realtime.py` drives scripted PCM/STT/LLM/TTS timing,
  barge-in, STT failure, LLM timeout, WebRTC VAD and cached crisis paths.
- `backend/tests/integration/test_realtime_voice_ws.py` asserts real WebSocket
  handshake/streaming/crisis behaviour; its security companion rejects missing
  or URL-only credentials, malformed session IDs and another user's session.
- Browser helper/player/component tests cover same-origin URL construction, PCM
  conversion, event validation, synchronous barge-in stop, crisis controls,
  microphone failure retry and socket-error type fallback. The Playwright test
  checks controls without requesting microphone permission.
- `make voice-sim` is the repeatable offline p50/p95 timing simulation. Exact
  command output and the manual-device checklist are recorded in
  [`docs/verification/day40-realtime-voice.md`](../verification/day40-realtime-voice.md).
