# ADR 0043 — Agentic care navigator: a tool-using AI behind every existing safety layer

Date: 2026-10-16. Status: accepted.

## Context

Days 1–43 shipped deterministic, single-purpose surfaces: chat, CBT,
journal, mood, assessments, voice. Day 44 asks for something the product
does not have — an AI that can *do* a small, well-defined set of things on
a person's behalf: search the curated support directory, draft a message
the person edits and sends themselves, prepare them for an appointment,
set a local reminder, and explain options. Each of those needs a decision
about which tool to use and with what arguments, which is what an agent
loop is for. The risk is equally clear: an agent is a new attack surface
(prompt injection through tool results), a new way to act in the world
(effecting tools), and a new place for safety to be forgotten
(the model is called *by* the loop, so "crisis detection before any LLM
call" must be enforced around the loop, not assumed inside it).

## Decisions

- **The agent sits behind the chat safety pipeline, never beside it.**
  Every inbound message passes the same `run_ensemble` + escalator the
  chat orchestrator uses (`AgentSafetyGate`). At risk >= MEDIUM the
  agent does not run — no prompt is built, no tokens are spent, no tool
  is considered — and the deterministic crisis flow answers with its
  pre-written copy and helplines. The agent only ever sees LOW. This is
  the whole safety argument in one place, and it reuses the chat
  pipeline's engine, classifier and escalator so the two paths cannot
  disagree.
- **The toolset is a closed allow-list of five tools, exactly the
  brief's five**: `search_directory`, `draft_message`,
  `make_appointment_prep`, `set_reminder`, `explain_options`. There is
  no browsing, payment, booking or sending tool *at all* — refusal is
  structural (the registry has no such name), not a prompt instruction
  the model might be talked out of.
- **Only `set_reminder` effects anything, and it pauses for a
  human.** Effecting tools build a confirmation card with an unguessable
  pending id and a 15-minute TTL; the loop returns
  `confirmation_required` and executes nothing until the API's
  confirmation endpoint answers `allow` with that id. The model cannot
  self-confirm — confirmation only exists on the resume path.
- **Tool results are data, never instructions.** Every result is
  sanitised (control characters stripped, markup removed, whitespace
  collapsed, length-capped), wrapped in an envelope that names the tool
  and says it is not instructions, and scanned with the same
  instruction-detector the output guard uses; a poisoned result is
  quarantined wholesale (the model sees a neutral placeholder) and the
  attempt is logged. The system prompt repeats the rule, but the
  defences do not depend on the model obeying.
- **Tool arguments are validated against strict JSON schemas**
  (Draft 2020-12, `additionalProperties: false`, closed enums, bounded
  lengths, ISO-8601 pattern for times) *before* execution; invalid
  arguments are rejected with a neutral error message fed back to the
  model so it can correct course in the same turn.
- **The loop is bounded**: at most 6 model calls and a 4000-token
  cumulative budget per turn (provider-reported usage where available,
  a chars/4 estimate otherwise). Hitting either stops the turn with an
  honest "I've taken as many steps as this turn allows" style message —
  never a silent truncation mid-tool-call.
- **Every tool call is in a transparent action log** with a closed
  event vocabulary (proposed, executed, rejected, confirmation required/
  allowed/denied/expired, injection quarantined, limits reached). The
  log carries validated arguments and neutral outcome summaries — never
  the person's raw message text (AGENTS.md rule 4).
- **Executors are deterministic templates.** No LLM runs inside a tool;
  directory text flows through unchanged (sanitised), drafts are the
  repo's reviewed templates, and every draft/prep/reminder output states
  plainly that the person edits and sends/acts on it themselves.
- **The model adapter is behind an interface** (`AgentLLM`), with a
  scripted `FakeAgentLLM` (tests, evals, offline default) and a lazy
  `AnthropicFunctionLLM`. Without an API key the navigator degrades to
  the fake with a startup warning rather than failing open.
- **Sessions are ephemeral and in-memory** (30-minute idle expiry,
  owned by user id; another user's session id is a 404, same as chat).

## Consequences / limits

The navigator cannot browse, pay, book or send — by construction, so it
cannot be tricked into those either (there is no code path). Its drafts
and prep sheets are starting points the person edits; the UI must keep
that framing. The fake fallback means a deployment without Anthropic
keys gets scripted, honest answers rather than model answers — a
deliberate offline-first default, and a known limitation. The action log
holds the turn in memory only; nothing is persisted. Injection defence
depends on the quarantine detector's recall, so the output guard still
runs on the final answer (one strict retry, then a safe fallback). The
directory it searches is only as current as its `last_verified` dates
(ADR 0044 covers its upkeep). Verified by the 40-scenario deterministic
eval (`make -C backend eval-agent`), the unit/integration tests
(confirmation gating, injection quarantine, step/token limits, gate
ordering) and the API integration tests (crisis phrase mid-conversation
never invokes the agent).
