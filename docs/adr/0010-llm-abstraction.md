# 0010 — The LLM provider layer: one interface, a chain that cannot fail

- Status: accepted (Day 10)
- Date: 2026-10-10
- Deciders: Manovia maintainers
- Supersedes: none
- Related: [0002 backend skeleton and conventions](0002-backend-skeleton-conventions.md),
  [0006 emotion model](0006-emotion-model.md),
  [0008 crisis detection rules engine](0008-crisis-detection-rules-engine.md),
  [0009 safety ML ensemble](0009-safety-ml-ensemble.md)

> **On the filename.** The Day 10 brief asks for `docs/adr/0003-llm-abstraction.md`.
> ADR numbers are permanent, and `0003` is already
> [the data layer ADR](0003-data-layer-models-migrations-and-encryption.md) —
> merged, referenced from PROGRESS.md and from the Day 3 notes. Renumbering a
> published ADR breaks every link to it, so this decision ships as **0010** and
> the filename is the only thing that differs from the brief.

## Context

Days 8 and 9 built the crisis gate that decides *whether* the model may speak at
all. Day 10 builds what happens when it may: the code that turns a redacted
conversation into a reply.

Three constraints shape everything below, and they pull in different directions:

1. **AGENTS.md rule 4/5** — every output passes a safety check, and raw message
   text is never logged at INFO or above. Identifiers never reach a vendor.
2. **The product is a wellbeing companion, not a chatbot demo.** Somebody who
   writes something difficult at 2 a.m. must not be met with an error page
   because a vendor had a bad minute. Degradation is a product feature here, and
   it has to be designed rather than hoped for.
3. **The sandbox has no API keys, and the test suite must pass offline.** Every
   external dependency sits behind an interface with a Fake (an existing project
   rule), and no model id may be hard-coded (also an existing rule).

The interesting question is not "which SDK" — it is *what happens when the SDK
is unusable*, and *what the model is allowed to see*.

## Decisions

### 1. One interface, three implementations, all behind `LLMProvider`

`app/services/llm/base.py` defines:

```python
class LLMProvider(ABC):
    async def complete(messages, *, system=None, max_tokens=400, temperature=0.7) -> LLMResult
    def stream(messages, *, system=None, max_tokens=400, temperature=0.7) -> AsyncIterator[str]
```

`stream` is abstract rather than "nice to have": a companion is read one token at
a time, and a provider that can only `complete` is a second-class citizen. The
interface declares it **without** `async`, which is a deliberate and load-bearing
detail — an `async def` with a `yield` is an async *generator*, and a caller
cannot tell from the signature whether "provider not configured" raises when the
method is called or when the first token is read. Providers that validate
(`anthropic`, `ollama`) are plain `def`s returning an inner async generator, so
they fail at call time; providers that only stream (`fake`, `canned`) are
`async def` generators.

Three implementations: **Anthropic** (hosted), **Ollama** (local, first
fallback), **Fake** (offline, scriptable, for tests). A fourth, `CannedProvider`,
is not really a provider — see decision 3.

**No default model id anywhere.** `AnthropicProvider` with no `ANTHROPIC_MODEL`
reports `is_configured = False`, and the chain moves on. A default model baked
into the code would quietly start costing money the day somebody forgot to set
the variable, and "which model answered?" would stop being answerable from
configuration alone. The same rule applies to `OLLAMA_MODEL`, with one
concession: when it is unset the provider asks the local server what it has
(`/api/tags`) and remembers the answer, because a developer running one model
should not have to spell its name twice.

The Anthropic SDK is imported **lazily, inside the client factory**, and lives in
an optional `llm` extra. Without it, `AnthropicProvider` raises
`ProviderNotAvailable` and the chain falls through — the same as a missing key.
The offline suite and CI never install it.

### 2. Typed errors, and the type decides the retry

`LLMError` carries a `retryable` class attribute. The retry machinery reads *that*,
not a list of exception names in a `try`/`except`, so adding a provider cannot
silently change retry behaviour:

| error | retryable | meaning |
| --- | --- | --- |
| `ProviderTimeout` | yes | exceeded the deadline |
| `ProviderRateLimited` | yes¹ | 429; carries `retry_after` when the vendor sent one |
| `ProviderDown` | yes | 5xx, connection refused, DNS, unknown SDK exception |
| `ProviderBadRequest` | no | 4xx, invalid credentials — retrying is how you get blocked |
| `ProviderNotConfigured` | no | no key, no model id — the expected state on a laptop |
| `ProviderNotAvailable` | no | optional SDK not installed |

¹ only when the wait fits inside the request's remaining budget.

No error in this tree carries message text. An exception can end up in a log
line or a trace report, and user text must never travel there.

### 3. The chain ends in a link that cannot fail

`primary → ollama → canned`. The last link is `CannedProvider`, which returns one
fixed string in this repository and cannot raise. That single fact is what makes
"a missing API key is a degradation, not a 500" true **by construction** rather
than by careful error handling in an endpoint: the chain has no code path that
returns nothing.

`AllProvidersFailed` exists, but only a chain built *without* the canned link can
raise it — which is a construction mistake, not an outage.

The canned reply is generic on purpose. It does not quote, paraphrase or react to
what the person wrote (a template cannot know whether its reaction is
appropriate), it says the failure is on our side, and it points at a helpline and
a trusted person. It is deliberately not warm-and-chatty: a degraded system
should not manufacture intimacy it cannot follow up on. Its exact words are in
`app/services/llm/canned.py` and therefore in a diff.

### 4. Retry, timeout and breaker are a wrapper, not a policy scattered around

`ResilientProvider` wraps any provider and is itself a provider, so the chain
sees one uniform interface and does not know which links are armoured.

- **Full jitter.** The delay is drawn uniformly from `[0, min(cap, base · 2ⁿ)]`,
  not set to the capped value. Without jitter, every client that was rate-limited
  at the same second retries at the same second — which is how a brief overload
  becomes a sustained one.
- **The timeout is a total budget, not a per-attempt one.** Three attempts at
  60 ms inside a 100 ms budget is two attempts. A provider that is slow-but-alive
  must not triple the latency a person waits through.
- **The breaker has a half-open probe.** After `failure_threshold` consecutive
  failures the provider is skipped for `cooldown_seconds`; after that one request
  is allowed through, and a success closes the circuit again.

Every knob (clock, sleep, RNG) is injectable, because the whole point of this
module is timing behaviour and a test that sleeps for real is a test nobody runs.

### 5. Redaction runs inside the chain, on the way out

`LLMChain._prepare` redacts every outbound message before the guard truncates
and windows it, before any provider sees it. Putting it in the chain rather than
in the caller means there is exactly one place where text can leave the process,
and that place is covered by the tests that assert on the Fake's recorded
payload.

The redactor (`app/services/nlp/redaction.py`) replaces identifiers with typed
placeholders — `[EMAIL]`, `[PHONE]`, `[ID]`, `[CARD]`, `[URL]`, `[PERSON]` — and
applies its patterns in a fixed order, longest-and-strictest span first, so a
16-digit card number is not also claimed as a phone number plus six digits.

Four judgement calls worth recording:

- **Over-redaction is the safe direction.** A 10-digit order number becomes
  `[ID]` and the model never sees it. Losing a detail the model did not need is
  free; leaking one it did not need is not recoverable.
- **URLs are replaced whole, not scrubbed.** Rewriting
  `https://x.test/cb?code=abc` to `https://x.test/cb?code=[REDACTED]` still tells
  the model where the user's account lives.
- **Names are opt-in.** `[PERSON]` needs an NER model, and the default
  `NullNameFinder` finds nothing. A name detector that fires on common Indian
  given names — many of which are also ordinary words (*Kiran*, *Jyoti*,
  *Anand*) — mangles sentences, and a redactor that is wrong half the time teaches
  engineers to switch redaction off. `LLM_REDACT_NAMES=true` turns it on behind
  the `NameFinder` interface.
- **The reversible map is dropped immediately.** `Redactor.redact` returns
  `placeholder → original` for the one legitimate case (restoring a person's own
  words in text that never left the process). The chain discards it in the same
  frame: nothing downstream needs the originals, and keeping the only copy of
  somebody's Aadhaar number alive after the call has no upside.

Property tests (hypothesis) assert what examples cannot: after one pass a second
pass finds nothing, the output is idempotent, and no generated email, phone
number, id or card survives anywhere in the result.

### 6. The prompt is a versioned file, not a string in the source

`app/content/prompts/system_v1.md`, loaded and validated by
`app/services/llm/prompts.py`. The filename **is** the version
(`LLM_PROMPT_VERSION=system_v1`), the file is SHA-256'd at load, and three
things are enforced at load time:

- only `{max_words}` and `{language}` exist as placeholders — a template that can
  interpolate arbitrary fields is a template that can interpolate user text into
  instructions;
- all ten rules from the Day 10 brief are present (`REQUIRED_RULES`), so a
  careless edit is a startup failure rather than a silent change in how the
  product talks to somebody in distress;
- the leading `>` blockquote — the note for human reviewers — is stripped before
  the model sees anything, so we are not paying to send our own documentation to
  a vendor.

`{language}` is rendered from a fixed table (`en → English`), and an unknown code
renders as "the language they wrote in" rather than being interpolated. Detection
answers `other` for short text, and "reply in other" is not an instruction a
model can follow — better to tell it to read the language off the message.

### 7. The token guard truncates, it never rejects

`TokenGuard` bounds two costs: one oversized message, and a long conversation
whose whole history is resent every turn. Both are handled by *truncation and
windowing*: over-long input is cut at a word boundary with a `[…]` marker, and
the window keeps the most recent turns that fit the budget, oldest dropped first.

Two rules inside the guard matter more than the budget: the newest turn always
survives however small the budget (dropping it would leave the model answering a
question it cannot see), and truncation cuts on a word boundary so a redaction
placeholder is never cut in half — `[PHO` is not a placeholder any more, and
would reach the provider as half an address the redactor no longer recognises.

The token estimate is deliberately crude (4 characters ≈ 1 token). Providers
report true usage in `LLMResult.usage`; this number only has to be conservative
enough to keep a request inside its budget.

## Alternatives considered

| alternative | why not |
| --- | --- |
| **LangChain / LiteLLM** | Both are large, both move fast, and both would put a dependency between us and the two things this product must control exactly: what leaves the machine, and what happens when the vendor is down. The interface here is ~120 lines and is covered by tests we wrote. |
| **A single provider with the fallback inline** | The fallback chain is the feature. Wrapping each provider in its own `ResilientProvider` means retry/breaker state is per-provider and testable in isolation; inline `try`/`except` chains are where "the third fallback was never tested" comes from. |
| **Redaction in the API layer** | Then every future caller has to remember it. One egress point in the chain is one place to audit and one place to test. |
| **Reversible tokens (`<PHONE_1>`) instead of typed placeholders** | Typed placeholders tell the model *what kind of thing* was removed, so it can say "I can't reach you at that number" instead of being confused; and they make a payload assertion readable. The reversible map is available for the rare case that needs it. |
| **A default model id so the product "just works"** | Violates the project rule against hard-coded model names, and turns a configuration omission into an unbudgeted bill. |
| **Streaming left for later** | Retrofitting streaming onto a `complete`-only interface means reworking every caller. It is in the interface from day one. |

## Consequences

**What gets better**

- A missing key, an invalid key, a dead Ollama, and a spent deadline all produce
  a warm, safe, pre-written reply. Nothing in the chain can return a 500.
- The offline suite exercises the whole layer: 286 new tests, no network, no
  keys, no model download.
- The prompt is reviewable by a non-engineer and is diffable in a pull request.
- Cost is bounded per request by two independent mechanisms (input truncation,
  windowing) plus an output ceiling.

**What gets worse / what is still open**

- **Nobody has run this against a real model.** Every test uses the Fake. The
  first real call is a manual check on a maintainer's machine (Day 10
  verification item 4), and the prompt has never been read by the model it is
  written for.
- **The provider's own retry loop is disabled** (`max_retries=0` on the SDK
  client, ours owns the deadline). If a future SDK does something useful there
  that ours does not, we have opted out of it.
- **`[PERSON]` is off**, so names reach the model. For a product whose users are
  mostly Indian, and where the NER models are English-centric, this is a real
  gap — it is a parking-lot item, not a solved problem.
- **The token estimate is wrong for Devanagari and Bengali**, which are
  multi-byte in UTF-8 and tokenise to more tokens per character than English.
  The window will therefore keep *more* Indic turns than intended. It errs
  toward spending more, not toward cutting a person off, but it is wrong.
- **Redaction over-matches.** Dates like `2024-01-15` and long order numbers
  become `[PHONE]` / `[ID]`. The model gets a slightly garbled sentence; the
  alternative direction is worse, but it is a real cost to the reply quality.
- **Nothing here is wired to an endpoint yet.** The chain is built, tested and
  configurable; Day 11's job is to put the crisis gate in front of it and let
  real traffic through.
