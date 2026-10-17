# ADR 0044 — The support directory is also a read-only MCP server

Date: 2026-10-16. Status: accepted.

## Context

Day 44 ships `backend/app/content/resources/directory.json`: 27 curated
support options (campus counselling, national helplines, free/low-cost
therapy directories, support groups), each with `source_url`, languages,
cost, hours and `last_verified`. Manovia's own agentic navigator searches
it internally. The brief also asks for the directory to be exposed to
*other* AI clients over the Model Context Protocol — a clinic's assistant
or a partner helpline bot could then find the same verified options —
without letting those clients write, book, pay or send anything, and
without them becoming part of Manovia's trust boundary.

## Decisions

- **A separate top-level module (`mcp-server/`), not a backend route.**
  The separation is the point: the MCP server shares only the data file,
  imports nothing from `app.*`, and can be deployed (or not)
  independently. It reads `directory.json` directly at startup and
  validates it against the shipped JSON schema, failing loud.
- **Read-only, structurally.** The server exposes exactly two tools —
  `search_directory` and `list_regions` — and rejects any other tool name
  with a JSON-RPC error. There is no code path that writes, books, pays
  or sends.
- **Authentication and rate limiting are required, not advisory.**
  Clients authenticate with a bearer token (`MCP_API_KEYS`,
  comma-separated, compared in constant time); a missing or wrong token
  gets HTTP 401. Each key has a token-bucket limit
  (`MCP_RATE_LIMIT_PER_MINUTE`, default 60); over it, HTTP 429 with
  `Retry-After`.
- **The same verification rule as the navigator.** Entries marked
  `needs_verification` are excluded from search results — only
  `verified` entries are served, and `list_regions` reports how many
  entries are currently excluded, so an operator can see the backlog.
- **Provenance travels with the data.** Every served entry carries its
  `source_url` and `last_verified`; the README tells operators to display
  both and to re-verify before relying on a number.
- **Transport is JSON-RPC 2.0 over HTTP** (`POST /mcp`), the
  streamable-HTTP shape MCP clients speak. The directory itself is
  transport-independent, so other transports can be added without
  touching the search logic.

## Consequences / limits

Partner clients get the same curated, verified data Manovia's navigator
uses — one file, one schema, one verification discipline. The costs: an
operator must provision keys per client and watch the rate-limit logs;
the data is curated, not live (an entry can drift between verification
passes, which is why `last_verified` is served alongside every entry);
and the server is only as trustworthy as its directory, so the
`needs_verification` exclusion is load-bearing and tested. The module
deliberately does not use Manovia's auth, sessions or consent machinery —
it is a separate trust domain with its own keys, and its README says so.
