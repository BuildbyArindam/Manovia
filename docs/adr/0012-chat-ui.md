# 0012 — The chat UI: reading a stream, and staying calm while doing it

Date: 2026-10-10
Status: Accepted

## Context

Day 12 puts the Day 11 SSE contract in front of a person. The backend answers
`POST /chat/sessions/{id}/stream` with `text/event-stream`: `token` events, then
a `final` event carrying `{reply, replaced, metadata}` where `metadata` holds
`risk_level`, `response_type`, `resources`, `emergency` and the pre-written
`crisis` copy. Everything the UI must decide is downstream of that shape.

Five constraints shape the design more than any component choice:

1. **The reply can stop at any byte.** A proxy, a mobile network or a closed tab
   can end the stream mid-sentence. What the UI does then is a safety question:
   a half-delivered helpline is worse than none.
2. **The crisis path must be unmistakable and must not be decorated.**
3. **One announcement per turn, not one per token.** Screen-reader users get the
   reply once; sighted users watch it arrive.
4. **Auto-scroll must never take the page away from someone reading.**
5. **Nothing is stored unless the person asked for it** (`store_chat`).

## Decisions

### 1. `fetch` + `ReadableStream`, not `EventSource`

`EventSource` cannot issue a `POST` and cannot set an `Authorization` header.
The chat stream needs both (AGENTS.md rule 5 keeps the message out of a URL, and
the token cannot go in a query string), so `features/chat/stream.ts` reads
`response.body` itself and feeds it to a small SSE parser (`sse.ts`).

The parser exists as its own module because the property that matters is not
parsing, it is **chunk-boundary safety**: a proxy may deliver one frame in seven
reads, or twelve frames in one. It dispatches only on a blank line, handles CRLF
and a lone CR, joins multi-line `data`, ignores comments and unknown fields, and
`flush()`es a trailing unterminated frame so a socket that dies after writing a
complete frame does not lose it. Unknown event names are ignored rather than
fatal: a newer server may add one.

`ApiClient.postForStream` (in `lib/api.ts`) owns the HTTP half so it is not
duplicated: refresh before the request, one replay after a 401, and a non-2xx
body turned into an `ApiError` with the backend's code and request id. That
matches the backend's own rule — problems known before the first byte are
ordinary HTTP errors, never in-stream events.

### 2. An interrupted stream keeps its words and says it was interrupted

A stream that ends without `final` yields `{kind: "interrupted", partial,
reason}`. The reducer keeps the text that arrived, marks the turn `incomplete`,
and the bubble says "This reply was cut off before it finished." The client
never resumes: a partial safety reply is not something to continue in the
background, and the honest affordance is "Ask again", which resends the exact
same message.

An abort the *user* asked for (a new chat mid-stream) is not an error: the
placeholder reply is dropped silently and no failure banner appears.

### 3. `final.reply` is authoritative

When `replaced` is true the streamed tokens were not the answer (the output
guard rewrote them, or the model failed part-way), so the rendered text is
swapped. The client does not try to reconcile the two.

### 4. One polite live region per turn

The brief asked for `aria-live="polite"` on the latest assistant message; the
implementation puts it there, and decides *what* that region contains. While
tokens stream, the visible text is `aria-hidden` and the region's accessible
content is one sentence — "Manovia is replying…". When the reply finishes, the
accessible content becomes the reply, so assistive technology announces exactly
once per turn. Announcing every token would be worse than silence, and WCAG
4.1.3 is about status messages, not about how much a page can shout.

Every bubble also names its speaker in a visually-hidden prefix ("You said",
"Manovia said"), because colour and alignment cannot carry who is talking.

### 5. Crisis: the card is the answer, and the page softens

`response_type === "crisis"` (or a HIGH/IMMINENT `risk_level`) renders the
existing `CrisisCard` inside the assistant's list item, immediately under the
reply, and sets `data-softened="true"` on the page. Softened mode removes the
starter chips, stops the typing-dot animation and keeps the only emphasis on the
card. There is no emoji and no confetti in this product; the flag is what stops
a future celebration from landing on a safety reply. The pre-written copy is
rendered verbatim — the client rewords nothing (AGENTS.md rule 2).

### 6. Auto-scroll follows only a reader who is already at the bottom

`useAutoScroll` tracks whether the container is within 64px of the bottom. New
content moves the view only while it is; scrolling up stops the view dead, and a
"Jump to latest" button appears. Jumping sets `scrollTop` rather than calling
`scrollTo({behavior:"smooth"})`: smoothness belongs in CSS (`scroll-behavior`),
which already collapses to `auto` under `prefers-reduced-motion`.

### 7. Ephemeral by default; the toggle is the consent surfacing

The session is opened lazily, on the first send, so loading the page creates
nothing. "Save this conversation" maps to `save_history` on
`POST /chat/sessions`, which needs the `store_chat` consent; without it the
backend answers `403 consent_required` and the page says "one more agreement
needed" with a link to Settings rather than failing quietly. The toggle applies
to the **next** session, and the text under it says so — an in-memory
conversation cannot be retroactively written to the database.

"Clear conversation" empties the screen and confirms first; it does not delete
stored rows and does not claim to. "Start a new chat" drops the session as well.

### 8. Failure copy is a small closed set, and the backend's wins

Eight kinds: offline, network, stream-interrupted, stream-error, consent,
rate-limited, session, unknown. Offline and network are different sentences
because they are different situations, decided by `navigator.onLine` plus the
`online`/`offline` events. When the backend already wrote a curated sentence
(`message_too_long`, `session_ended`, …) that sentence is shown instead of a
second copy that would drift. Every banner is a polite live region, never
`role="alert"`: an assertive interruption is the wrong register here.

Retry resends the exact text that failed, and nothing else.

### 9. The emotion hint is opt-in and off

"Show how I read your mood" (Settings, default off, stored locally) adds one
muted line — "Reads as low" — under a reply. It maps labels to plain words and
drops the confidence score, because a percentage next to "sadness" would read as
a measurement. On a crisis turn it says nothing: the pipeline stops before
emotion analysis, and labelling a safety reply with a mood would be exactly
wrong.

### 10. One IME detail that matters for this product

Enter sends, Shift+Enter makes a new line — and an `Enter` with
`isComposing === true` does **not** send. Hindi and Bengali input methods emit
Enter to commit a candidate; sending on that keystroke would cut a word in half
in the two languages this app exists to serve.

## Alternatives considered

- **`EventSource` with the token in a query string.** Rejected: it puts a bearer
  token (and, on the GET stream form, the message) in URLs, which proxies and
  CDN logs record. The GET form exists for `curl` only.
- **Optimistic rendering with a background reconnect and resume.** Rejected for
  now: resume needs an event-id contract on the backend, and a half-delivered
  safety reply is the one thing that must not be assembled silently.
- **React Query mutation for the stream.** Rejected: the value is in the
  incremental events, and a mutation's single settle point hides them. TanStack
  Query still owns the helpline and consent fetches, where caching is the point.
- **A virtualised message list.** Not yet; it would complicate the live region
  and the auto-scroll contract for a conversation length the ephemeral store
  caps anyway.

## Consequences

- The SSE reader is 90 lines that must stay chunk-boundary safe; `sse.test.ts`
  pins that, including a frame split inside a field name and a flush after a
  dead socket.
- `postForStream` duplicates a little of `send()` (auth, refresh, error
  envelope). Accepted: the alternative was a streaming branch inside the JSON
  path, which is worse to read.
- Because the browser cannot be exercised in this sandbox, the shipped client is
  verified against a live backend by `liveBackend.test.ts` (opt-in via
  `MANOVIA_LIVE_API`) and the Playwright suite is written but not run here. See
  PROGRESS.md → Verification (Day 12) for exactly which checks need a machine
  with a browser.
- The softening flag is UI-wide but only the chat page reads it today; if mood
  or journal surfaces grow celebrations, they must read the same flag.
