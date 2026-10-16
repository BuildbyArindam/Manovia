# ADR 0040 — In-browser private AI: compiled safety content, a ported rules engine, and consented on-device models

Date: 2026-10-16. Status: accepted.

## Context

Everything Manovia does today happens on the server: the rules engine reads
`backend/app/content/safety/*.yaml`, the orchestrator runs crisis detection
before the LLM, the output guard checks model text, and the transcript is
encrypted with a server-side key hierarchy (ADR 0017, ADR 0022). That is a good
privacy story for a service, and a weak one for a person who cannot or will not
send what they are thinking to somebody else's machine — a shared device, a
monitored household, a workplace network, or simply a bad day in a country where
"mental health" is a searchable word in a log.

Day 41 asks for a Private Mode in which **nothing leaves the device**: no message
text to the server, none to any LLM API. The safety pipeline has to be present in
the browser, and it has to behave *identically*, because a private mode that
classifies risk differently is a second, worse product.

Three constraints shaped this decision:

1. **Safety parity is not negotiable.** AGENTS.md rule 1: crisis detection runs
   before any LLM call and is deterministic. In Private Mode the deterministic
   part is the *only* part that is guaranteed, so it must be the same code path
   in behaviour, not a reimplementation in spirit.
2. **This sandbox has no WebGPU, no model egress and no microphone.** Chromium
   153 here exposes `navigator.gpu` but `requestAdapter()` returns null, and
   Hugging Face is unreachable. Anything that depends on real inference cannot be
   verified here, so it must be behind an interface with a fake, and the honest
   claim has to be written down rather than demonstrated.
3. **Model weights never enter the repository** (AGENTS.md ML ground rules), and
   an unverified model binary is a supply-chain hole in a product whose whole
   pitch is "nothing leaves your device".

## Decision

### 1. Compile the safety content into one same-origin JSON bundle

`frontend/scripts/build-private-bundle.mjs` reads `backend/app/content/**`
(YAML rules, helplines, i18n templates, prompt Markdown) at build time and writes
`frontend/public/private/safety-bundle.json` — 191.5 KB, sha256
`9fa233a8ea9ff143`, format `manovia-private-bundle/1`. It is fetched at Private
Mode enable time with `credentials: "omit"`, never imported as a JS chunk, and
its equality with the content is a CI gate (`--check`) on both sides:
`backend/tests/safety/test_private_bundle.py` (25 tests) and
`src/features/private/bundle/drift.test.ts`.

The three system prompts ship inside the bundle. That costs ~12 KB and buys the
ability to run the output guard's prompt-leak check (RT-I-SP-01..03) against real
prompt text in the browser; both sides pin that the shipped prompt equals the
file on disk.

Alternatives rejected: *generating the bundle at runtime in the browser* (needs
the YAML and a parser in the client, and cannot be gated); *duplicating the
content in TypeScript* (two sources of truth for safety rules, which drift
silently).

### 2. Port the rules engine; do not reimplement it

`rules/pyStrings.ts` and `rules/pyRegex.ts` reproduce CPython semantics that
JavaScript does not share: `str.casefold` (352 code points where Python's
casefold and JS `toLowerCase` disagree are shipped as data, generated on both
sides), `\b` over `[\p{L}\p{N}_]`, `[\p{Nd}]` for `\d`, a 29-code-point
whitespace class, and the `u` flag on every pattern. `rules/ruleEngine.ts` walks
the compiled rules in the server's order with no extra tie-breakers, because a
tie-breaker that Python does not have is a divergence nobody would notice.

Parity is a **500-phrase corpus** built from the content
(`evals/parity/corpus.jsonl`), with Python's verdicts exported to
`python-expected.json` and compared field by field: level, categories, ordered
rationale codes and ten context fields. 500/500 identical. The same treatment
covers normalisation (126 probes), the output guard (36 probes, 25 refused) and
96 crisis plans. `make parity` runs all of it.

Alternatives rejected: *a shared WASM build of the Python engine* (a real option
long-term, but it puts Python's runtime in the browser and cannot be gated by the
existing pytest suite); *"close enough" thresholds* (risk levels are the product).

### 3. Models are consented, pinned, resumable and deletable — and optional

`models/downloadManager.ts` is built on interfaces (`ModelSink`, `RemoteFetcher`,
`Hasher`, `KeyValueStore`, `RuntimeInstaller`) so it is fully testable offline.
Consent is recorded with the declared byte size before a byte moves; downloads
resume with HTTP Range; bytes are hashed against the manifest and a mismatch
fails closed; model bytes live in **IndexedDB, not the Cache API**, because the
Cache API is origin-scoped, evictable and observable by service-worker code that
has no business reading model state.

`scripts/pin-models.mjs` fills in sha256 hashes from upstream and refuses to
write a manifest it could not verify. It needs egress this sandbox does not have,
so the committed manifest is **deliberately unpinned** and every model reports
itself not installable until `npm run models:pin` runs somewhere that can reach
the upstream. Failing closed beats shipping a hash typed in by hand.

WebLLM keeps and verifies its own artifact cache (`managed_by_runtime: true`);
that is recorded as a known issue rather than papered over, because the
alternative — intercepting another runtime's cache — is worse.

### 4. No model runtime is a dependency

`@huggingface/transformers`, `onnxruntime-web` and `@mlc-ai/web-llm` are **not**
installed. `models/web/moduleLoader.ts` injects the dynamic import, so the
adapters (`transformers.ts`, `webllm.ts`) are tested against stubs and the bundle
does not grow for people who will never use Private Mode. The only new
dependencies are dev-only: `js-yaml` (the compiler) and `fake-indexeddb` (tests).

The consequence, stated plainly: on a device without WebGPU, or before the
manifest is pinned, Private Mode answers with the **template writer** — reviewed,
localised, deterministic copy — and says so in the reply badge. A smaller answer
that is honest beats a larger one that is invented.

### 5. Capability detection decides the tier, and rules-only is still a product

`runtime/capability.ts` probes WebGPU (including a real `requestAdapter()`), WASM
SIMD and threads, cores, `deviceMemory`, storage estimate, IndexedDB, Cache API,
WebCrypto, service workers and the secure context, and returns one of three tiers
— `full`, `classifiers`, `rules-only` — with blocking reasons and one
plain-language sentence each. `usable` stays true at `rules-only`: deterministic
crisis detection with real helplines, offline, is worth having on a device that
will never run a model. The panel offers standard mode instead of pretending.

### 6. Storage is encrypted with a key the server never sees

`crypto/passphraseKey.ts`: PBKDF2-HMAC-SHA256, 210,000 iterations, 16-byte salt,
AES-GCM-256 keys that are never extractable, per-field AAD
(`manovia:private:v1|conversation=<id>|field=<role>`). The keyId is derived from
the passphrase (AES keys cannot be exported to be hashed), so a wrong passphrase
and a tampered record are indistinguishable — which is the right property.
Message records are cleared to `id, conversationId, seq, format, ciphertext, iv,
keyId`; role, text, level and timestamps live inside the ciphertext.

There is no recovery. The UI says so before the key is created. Losing the
passphrase loses the transcript, and pretending otherwise would mean storing
something that defeats the encryption.

### 7. The session mirrors the server pipeline, in order

`session/privateSession.ts`: validate → settings → rules engine → safety
classifier (may raise caution from none/low to medium, never lower — the
existing ML ground rule) → escalation plan → **crisis short-circuit with no
model** → emotion classifier → reply → output guard over the model's text *before*
the check-in suffix is appended → encrypted store → in-memory ring buffer (six
turns). A storage failure never costs the reply (`persisted: false`).

`session/private-session-no-network.test.ts` scans the source of every module
that holds message text and fails on any network call, dynamic import or API
client import; `e2e/private-mode.spec.ts` proves it behaviourally with all
networking blocked twice over.

### 8. Private Mode is loaded on demand, not shipped to everybody

The feature first landed imported statically into `ChatView`, and the Day-25
bundle budget said no: `/chat` may add 25 KB gzip and had added 42.5 KB, and the
precache may hold 800 KB raw and had reached 885.2 KB. `npm run budget` is a gate
in the Accessibility workflow, so this was a build failure, not a taste question.

The budgets were not raised — raising them would let every route grow by whatever
it likes next time. Instead:

- `ChatView` renders `PrivateModeEntry` (a heading, one sentence and a button) and
  loads `PrivateModeProvider` with `React.lazy`. The provider owns the hooks, the
  locale sync and the panel, so the split is one line and the whole stack — rules
  engine, capability probe, download manager, crypto, panel — travels in a chunk of
  its own (96.3 KB raw / 30.8 KB gzip).
- `usePrivateMode` takes `autoBoot: false` from the chat page: opening the panel is
  not turning Private Mode on, so neither the boot's two fetches nor the 191 KB
  bundle are requested until somebody flips the switch. The panel is a button rather
  than a switch for the same reason — the consent copy has to be on screen first.
- The chunk is excluded from the precache exactly like the chart library is
  (`PRECACHE_EXCLUDED_CHUNKS`): a background download at install is the one thing
  worse than a lazy fetch. The service worker does keep the two compiled artifacts
  after that, network first with a 30-day expiry, because a deploy that changes a
  safety rule must reach the device the next time it is online; serving stale rules
  from cache would be a safety regression. Model bytes are excluded from that route
  by path shape, so the consented, hash-verified download manager stays the only way
  they arrive.

The cost is honest and narrow: the first time somebody opens Private Mode needs a
connection. `Suspense` keeps the standard chat page usable while the chunk loads, a
failed boot reports itself and leaves the switch off, and `ChatView.private.test.tsx`
pins all three behaviours.

## Consequences

**Better:** a conversation that is genuinely local; identical safety decisions on
both sides, gated by tests rather than by review; no new runtime dependency in
the shipped bundle; models that cannot be fetched silently or run unverified.

**Worse:** two implementations of the same rules to keep in step (mitigated by
six exporter `--check` gates and the 500-phrase corpus, but it is real work on
every content change); ~191 KB fetched at enable time; a reply runtime that is
weaker than the server's, and on most devices today no reply model at all.

**Known gaps:** WebLLM verifies nothing; the pinned hashes must be produced on a
machine with egress; Private Mode has no region source, so it shows the
international helpline set (Find A Helpline, Befrienders Worldwide, 112) rather
than a country-specific one — the region lives server-side today, and Private
Mode does not ask; voice is unavailable and says so.

**Follow-ups:** a region picker for Private Mode; a distilled safety classifier
(the manifest slot exists and is honestly marked "not shipped yet" — a general
toxicity model is not a substitute); measuring tokens/second and memory on a real
WebGPU device.
