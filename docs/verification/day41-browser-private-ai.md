# Verification — Day 41 in-browser private AI ("Private Mode")

**Date:** 2026-10-16
**Scope:** the compiled safety bundle, the ported rules engine and its parity with
Python, capability detection, the consented model download manager, encrypted
local storage, the private session and its UI, and the claim that nothing leaves
the device.

Every number below was produced by running the command shown, in this sandbox, on
2026-10-16. Where the sandbox cannot measure something (WebGPU inference, a real
model download, tokens/second), the row says so instead of guessing. The
implementation decisions are in [ADR 0040](../adr/0040-in-browser-private-ai.md).

**Machine:** 2 vCPU (`navigator.hardwareConcurrency = 2`), `deviceMemory = 4` GB,
Chromium 153.0.8010.0 headless (`@sparticuz/chromium`, SwiftShader), Node 22,
Python 3.11.2. `navigator.gpu` exists but `requestAdapter()` returns `null`, so
**no WebGPU device is available** and the reply model cannot run here.

## Summary

| # | Check | Command | Result |
|---|---|---|---|
| 1 | Parity: 500 phrases, identical risk levels in Python and TypeScript | `make parity PYTHON=…/.venv/bin/python` | **PASS** — 500/500 identical, 0 mismatches |
| 1a | Six exporter gates (bundle, corpus, expectations, normalisation, output guard, crisis plans, casefold) | `make -C backend parity` | **PASS** — all `--check` OK |
| 1b | Committed bundle equals the content on disk | `pytest tests/safety/test_private_bundle.py -q` | **PASS** — 25 passed |
| 2 | Playwright, all network blocked: ordinary message + HIGH-risk phrase + crisis card | `npx playwright test e2e/private-mode.spec.ts` | **PASS** — 4 passed (both viewports) |
| 3 | Normal Private Mode session: zero requests carrying message text | instrumented Playwright run, network up | **PASS** — 0 requests during a 3-message conversation |
| 4 | Download manager: consent, resume, corrupt chunk, delete | `npx vitest run src/features/private/models` + panel/hook tests | **PASS** — 23 + 22 + 12 tests |
| 5 | Load time, memory, tokens/second on this machine | instrumented Playwright run (CDP `Performance.getMetrics`) | **PARTIAL** — timings and memory measured; tokens/second **not measurable** (no WebGPU adapter) |
| 6 | Local storage is encrypted (raw IndexedDB inspection) | instrumented Playwright run, `indexedDB.open("manovia-private")` | **PASS** — ciphertext only, no plaintext, no passphrase |
| 7 | Backend suite and lint untouched | `pytest tests/safety -q`, `make lint` | **PASS** — 882 passed; ruff + mypy clean (343 files) |
| 8 | Frontend suite, types, lint, build | `npx vitest run`, `npx tsc --noEmit`, `npx eslint .`, `npm run build` | **PASS** — 2614 passed (94 files); 0 type errors; 0 lint problems; production build OK |
| 9 | Standard mode not broken by the chat-surface changes | `npx playwright test e2e/chat.spec.ts` | **PASS** — 14 passed (both viewports) |

## 1. Parity — 500 phrases, identical verdicts

```
$ make -C backend parity PYTHON=/home/user/.venv/bin/python
OK corpus.jsonl: 500 messages
OK python-expected.json: 500 cases, levels {'high': 195, 'imminent': 23, 'low': 22, 'medium': 14, 'none': 246}
OK python-normalisation.json: 126 probes
OK python-output-guard.json: 36 probes, 25 refused by Python
OK python-crisis-plans.json: 96 plans
OK frontend/src/features/private/unicode/python-casefold.json: 1671 cases, sha256 41420f2c4439ce53
25 passed in 3.95s
```

```
$ make parity PYTHON=/home/user/.venv/bin/python     # … frontend half
Test Files  17 passed (17)
     Tests  1990 passed (1990)
```

`src/features/private/rules/parity.test.ts` compares Python's exported verdicts
with the TypeScript engine over all 500 corpus phrases, field by field: risk
level, category set, the *ordered* rationale codes and ten context fields
(`about_someone_else`, negation, subject, quotation and so on).

**Mismatches: none.** 500/500 identical, ≈0.9 ms per message. The corpus is built
from the shipped content (`evals/parity/build_corpus.py`), not hand-written, so a
new rule adds phrases automatically and `--check` fails until Python's
expectations are re-exported.

The bundle under test is 191.5 KB, sha256 `9fa233a8ea9ff143`, format
`manovia-private-bundle/1`: 215 rules, 66 output rules, 66 check-in phrases, 34
helplines, 3 locales, 352 casefold overrides, 3 system prompts.

## 2. Playwright with the network blocked

```
$ LD_LIBRARY_PATH=/tmp/al2023/lib:/tmp/sw \
  PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/tmp/chromium \
  PLAYWRIGHT_CHROMIUM_ARGS='["--no-sandbox","--disable-dev-shm-usage","--no-zygote",
    "--use-gl=angle","--use-angle=swiftshader","--enable-unsafe-swiftshader"]' \
  npx playwright test e2e/private-mode.spec.ts --reporter=list

Running 4 tests using 1 worker
  ✓  1 [mobile-360x640]  › answers an ordinary and a crisis message with every request blocked (5.8s)
  ✓  2 [mobile-360x640]  › stores the transcript encrypted, and only on this device (6.3s)
  ✓  3 [desktop-1280x800] › answers an ordinary and a crisis message with every request blocked (2.6s)
  ✓  4 [desktop-1280x800] › stores the transcript encrypted, and only on this device (3.8s)
  4 passed (20.5s)
```

The block is applied twice: `context.setOffline(true)` and a catch-all
`page.route("**/*", route => route.abort())` that also records every attempt.

**What the reader saw** (captured from the same run, replies verbatim):

Ordinary message — `I had a bad day and I cannot switch my brain off`:

> Thank you for saying it out loud.
> Would you tell me a little more about what today has been like?
> I'm here, and we can take this one step at a time.
> *Manovia could not reach its language model just now, so this is a short
> written reply instead.*
>
> Answered on this device by Template replies — no model on this device (en,
> reviewed templates) · Not stored — this conversation lives in memory only

MEDIUM message — `nothing matters any more` (the check-in paragraph is appended
only at MEDIUM, exactly as the server does):

> … We can keep talking. If hearing a person's voice would help, the helplines
> below are free and confidential — you don't need to be in crisis to use them.

HIGH-risk message — `I have a plan to kill myself tonight and I have the pills`:

> **Please get help right now — you don't have to do this alone**
> I'm really glad you told me. What you've described needs a person, not an app,
> and it needs one now. Please call 112, or go to the nearest emergency
> department…
>
> *CrisisCard:* 112 — international emergency number · Find A Helpline
> (DEFAULT · 24/7 · 175+ countries) · Befrienders Worldwide · "Manovia is a
> self-help companion. It is not therapy, it cannot diagnose, and it is not a
> crisis service."
>
> Answered on this device by deterministic crisis copy (no model) · At this risk
> level the reply is pre-written and shown with helplines. No model writes it.

`data-softened="true"` on the chat view: the crisis turn drops the starter chips
and the animation, as it does in standard mode.

**Network during the blocked conversation: 0 attempts.** No off-origin request,
no request body containing message text, no `/api/v1/chat` call.

The helplines shown are the international set because Private Mode has no region
source (see Known issues).

## 3. Network log of a normal Private Mode session

A second instrumented run kept the network **up** and the backend **running**
(`LLM_PROVIDER=fake`), recorded every request the page made, then sent the same
three messages:

```
normal.requests_during_conversation | 0
normal.request_urls                 | []
normal.any_url_or_body_with_message_text | false
normal.any_chat_endpoint            | false
```

Zero requests: not "requests without the text", but none at all. For contrast,
the standard-mode run in the same session logs
`POST /api/v1/chat/sessions/{id}/stream` with a `chat_turn` event on the server —
so the harness was certainly able to see traffic. The backend's own log confirms
its existing discipline: `chat_turn` records `text_sha` and `text_length`, never
the message.

Boot does fetch two same-origin static assets, before Private Mode is on:
`GET /private/safety-bundle.json` (196,419 bytes transferred, 4 ms) and
`GET /private/model-manifest.json` (3,720 bytes, 5 ms), both with
`credentials: "omit"` and no body. `private-session-no-network.test.ts` (22
tests) enforces the same rule statically over every module that holds message
text.

## 4. Model download manager

No model can be downloaded in this sandbox: the committed manifest is
deliberately unpinned (Hugging Face is unreachable) and names no reply model at
all — which one WebLLM offers is `PRIVATE_LLM_MODEL_ID` in `.env.example`, the
only place this repository may name a vendor model, and it is unset — so
`installBlocker()` reports `integrity hash missing for … (run npm run models:pin)`
for the emotion classifier and `not shipped yet` for the safety and reply slots,
and the panel shows that sentence instead of a download button. The manager is therefore verified against
fakes and against the hook:

| Behaviour | Where verified | Result |
|---|---|---|
| Consent recorded with the declared byte size before any byte moves | `downloadManager.test.ts` | PASS |
| Interrupt then resume with HTTP Range (206 ⇒ `resumed: true`) | `downloadManager.test.ts` | PASS |
| Corrupted chunk ⇒ `integrity-failed`, state `failed`, bytes not kept as usable | `downloadManager.test.ts` (`InMemorySink.corrupt`) | PASS |
| Abort / cancel mid-download releases the registration | `downloadManager.test.ts` | PASS |
| `deleteModel` / `deleteAll` free the bytes; consent records survive | `downloadManager.test.ts`, `usePrivateMode.test.tsx` | PASS |
| Unpinned model refuses to download (`not-installable`) | `usePrivateMode.test.tsx` in a real boot | PASS |
| Pause/resume/cancel/verify/delete + confirm-before-delete-all in the UI | `PrivateModePanel.test.tsx` (22 tests, incl. axe) | PASS |
| Progress announced in exactly one `aria-live` region | `PrivateModePanel.test.tsx` | PASS |

`models/downloadManager.test.ts`: 23 tests. Model bytes live in IndexedDB
(`blobs`), not the Cache API.

## 5. Load time, memory, tokens/second — this machine

Measured in Chromium 153 headless over CDP (`Performance.getMetrics`), dev server:

| Measurement | Value |
|---|---|
| Onboarding + navigate to /chat (122 resources) | 2,181 ms, 1,464,753 bytes (dev, unminified) |
| Private Mode boot: switch on → verdict on screen | **92 ms** (143 ms on a cold run) |
| Safety bundle fetch | 196,419 B transferred / 196,119 B decoded, 4 ms |
| Model manifest fetch | 3,720 B, 5 ms |
| JS heap before Private Mode | 15.85 MB used / 28.60 MB total, 2,065 DOM nodes |
| JS heap after boot (bundle parsed, 215 rules compiled, IndexedDB open) | 19.22 MB / 34.63 MB, 2,241 nodes — **+3.4 MB** |
| JS heap after three turns incl. the crisis card | 27.30 MB / 40.13 MB, 2,614 nodes |
| First turn (rules warm-up): assess / reply / total | 173.5 ms / 32.5 ms / **231.8 ms** |
| Second turn: assess / reply / total | 5.3 ms / 2.0 ms / **30.6 ms** |
| Crisis turn (no model): assess / reply / total | 14.8 ms / 23.5 ms / **38.3 ms** |
| Tokens counted for the guard's length rule | 32 and 49 (per reply) |
| **Tokens/second** | **not measurable here** — `requestAdapter()` returns null, so no model ran |
| Production build | `npm run build` OK; ChatPage chunk 42.1 KB raw / 12.7 KB gzip, Private Mode in its own lazy chunk 96.3 KB raw / 30.8 KB gzip; `dist/private/` = 192 KB bundle + 4 KB manifest, fetched only on enable |
| Bundle budget (`npm run budget`) | **PASS** — `/chat` route increment 12.7 KB gzip of its 25 KB allowance, precache 791.5 KB of 800 KB. **FAIL before the split** (42.5 KB and 885.2 KB); see below |

**The budget gate caught the integration, and it was right to.** With Private Mode
imported statically, `npm run budget` failed both of its numbers: `/chat` allows a
25 KB gzip increment and had 42.5 KB, and the precache allows 800 KB raw and had
885.2 KB — every visitor to chat was downloading the rules engine, the capability
probe, the download manager and the crypto, and the PWA was precaching it at
install for people who would never turn Private Mode on. The budgets were not
raised. `ChatView` now imports `PrivateModeProvider` with `React.lazy` behind a
small entry point, `usePrivateMode` takes `autoBoot: false` so opening the panel
does not fetch the 191 KB bundle either, and the chunk is excluded from the
precache exactly like the chart library is. Both numbers are now inside budget and
the day25 production suite still passes (33 passed, 13 skipped).

The first message costs ~170–245 ms because 215 compiled regular expressions run
for the first time; every later message is 3–40 ms. A WebGPU device would add
model load time (seconds, once) and then generate at whatever the device manages.

**Devices that are NOT suitable for the model tiers** (from the capability policy
in `runtime/capability.ts`, and what it reported here):

- **No WebGPU device** — this sandbox; Safari and Firefox on most platforms today;
  any Linux box without a GPU driver; most Android WebView. Result: `rules-only`,
  replies come from the template writer.
- **Fewer than 4 cores or less than 4 GB `deviceMemory`** — the LLM tier is not
  offered even with WebGPU (WebLLM's own guidance is 4).
- **Fewer than 2 cores, or no WASM SIMD/threads** — the classifier tier is not
  offered either.
- **Not a secure context** (`http://` on a LAN address, an `iframe` without
  `allow`) — WebCrypto is unavailable, so encrypted storage is unavailable and
  `usable` is false. jsdom reports this, which is why the unit tests see
  `blocking: ["secure-context"]`.
- **Less than ~256 MB free storage plus the model size** — the manager refuses
  before it starts.
- Devices under aggressive storage eviction (iOS Safari private tabs): the
  transcript can disappear, which is survivable only because nothing is
  recoverable anyway and the UI says the conversation is on this device.

## 6. Encrypted local storage — raw IndexedDB inspection

Read from the page after a stored turn, with a passphrase set:

```
idb.stores              | ["blobs","messages","state"]
idb.message_count       | 2                       (the user turn and the reply)
idb.message_fields      | ["ciphertext","conversationId","format","id","iv","keyId","seq"]
idb.first_message       | { id: "private-mva9trhu-ulqhy4#00000000",
                            conversationId: "private-mva9trhu-ulqhy4", seq: 0,
                            format: "manovia-private-record/1",
                            keyId: "e747401b5359d545" }   (iv and ciphertext are Uint8Array)
idb.contains_plaintext_message | false
idb.contains_plaintext_crisis  | false
idb.contains_passphrase        | false
idb.settings_fields     | ["consent","keyId","keyIterations","keySalt","locale",
                           "privateMode","region","storeTranscript","verifier"]
idb.blob_keys           | 0                        (no model downloaded)
```

Role, text, risk level, timestamps and the crisis flag are inside the ciphertext;
the record carries only routing data. Settings hold key material
(salt/iterations/verifier) and never the passphrase. A wrong passphrase returns
`wrong-passphrase`, which is deliberately indistinguishable from a tampered
record. `crypto/passphraseKey.ts` is covered by `store/localStore.test.ts`
(23 tests) including the tamper cases.

## 7–9. Suites and lint

```
$ cd backend && /home/user/.venv/bin/python -m pytest tests/safety -q
882 passed, 48 warnings in 28.96s

$ cd backend && PATH=/home/user/.venv/bin:$PATH make lint
ruff check .            → All checks passed!
ruff format --check .   → 408 files already formatted
mypy app tests          → Success: no issues found in 343 source files

$ cd frontend && npx vitest run
Test Files  94 passed | 1 skipped (95)
     Tests  2614 passed | 3 skipped (2617)

$ cd frontend && npx tsc --noEmit      # no output, exit 0
$ cd frontend && npx eslint .          # no output, exit 0
$ cd frontend && npm run build         # OK — private:bundle → tsc → vite build → PWA
$ cd frontend && npx playwright test e2e/chat.spec.ts   # standard-mode regression
14 passed (53.1s)
```

`npm run private:check` (three `--check` gates: JS lowercase table, safety
bundle, model manifest) is wired into `make parity` and into `npm run build`.

## Known issues and what the operator must check

1. **`npm run models:pin` has never been run** — no Hugging Face egress here. The
   manifest ships unpinned and every model reports itself not installable. Run it
   on a machine with egress, review the hashes, commit the manifest.
2. **No real inference was executed anywhere.** transformers.js and WebLLM are
   adapters behind an injectable module loader, tested against stubs (24 tests).
   On a WebGPU device: download the emotion classifier, confirm the labels, set
   `PRIVATE_LLM_MODEL_ID`, re-run `npm run models:manifest`, download that model
   through WebLLM, and measure tokens/second and memory with a real model.
3. **WebLLM verifies nothing** — it manages its own artifact cache
   (`managed_by_runtime: true`), so once a model *is* named `installBlocker()`
   returns null for it and the integrity check does not cover its bytes. In the
   committed manifest the slot is unnamed and refused before that question arises;
   `webllm.ts` also refuses to guess a model reference instead of falling back to
   the entry's own id.
4. **Private Mode shows international helplines** — the region lives server-side
   today and Private Mode does not ask for it, so `resourcesFor(null)` returns the
   DEFAULT set (112, Find A Helpline, Befrienders Worldwide). A region picker is
   the fix; it is in the parking lot.
5. **The distilled safety classifier does not exist yet** — the manifest slot says
   `planned` with an honest note. Rules alone decide risk, which is the safe
   direction (models may only raise caution).
6. **`npm run format:check` fails on three files that predate this branch**
   (`src/features/chat/Composer.tsx`, `src/features/chat/VoiceRecorder.tsx`,
   `src/scripts/playwrightFailures.test.ts`). Left alone on purpose — Day 41 does
   not reformat other days' work. Every file this branch adds or touches is
   Prettier-clean.
7. **Opening Private Mode for the first time needs a connection**, because the
   chunk is lazy and deliberately not precached. The service worker keeps the two
   compiled artifacts after that (`manovia-private-v1`, network first, 30 days), so
   later boots work offline; a first boot that cannot fetch the chunk reports
   "Private Mode could not start" and leaves the standard chat page working.
8. **Microphone, WebGPU, WebXR and Bluetooth cannot be tested in this sandbox** —
   voice is disabled in Private Mode and says why.
