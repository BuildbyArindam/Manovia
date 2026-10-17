# Manovia support directory — read-only MCP server

An **optional, clearly separated** module (Day 44). It exposes
Manovia's curated, verified support directory
(`backend/app/content/resources/directory.json`) over the
Model Context Protocol so *another* AI client — a clinic's own
assistant, a partner helpline bot, an operator's tooling — can
search it. The server is deliberately read-only: it has exactly
two tools and no code path that writes, books, calls, sends or
browses.

```
mcp-server/
├── README.md     this file
├── server.py     the server (FastAPI, JSON-RPC 2.0 over HTTP)
├── client_demo.py  a minimal MCP client you can run now
└── test_server.py  auth, rate-limit and behaviour tests
```

## What it exposes

| Tool | What it does |
|---|---|
| `search_directory` | Search verified entries by `region`, `language`, `need`, `budget` (all optional). Returns at most 8 entries: name, kind, regions, description, contact, languages, cost, hours, needs, source_url, last_verified. |
| `list_regions` | Region codes covered, entry counts per region, and how many entries are currently excluded as `needs_verification`. |

Entries marked `needs_verification` are **excluded** from search
results, exactly as they are inside Manovia itself. Only
`verification_status: "verified"` entries are served.

## Running it

```bash
cd mcp-server
MCP_API_KEYS=clinic-key-1 /path/to/venv/bin/python server.py
# → http://127.0.0.1:8787/mcp
```

Configuration (environment only, no secrets in code):

| Variable | Meaning | Default |
|---|---|---|
| `MCP_API_KEYS` | Comma-separated bearer tokens, one per client. **Required.** | — |
| `MCP_RATE_LIMIT_PER_MINUTE` | Requests per key per minute. | `60` |
| `MCP_DIRECTORY_PATH` | Path to `directory.json`; default is the backend's shipped copy. | shipped file |
| `MCP_HOST` / `MCP_PORT` | Bind address / port. | `127.0.0.1` / `8787` |

## How another AI client uses it

Any MCP-capable client that speaks HTTP JSON-RPC 2.0 can
connect. Point it at `POST /mcp` with a bearer token:

```json
POST /mcp
Authorization: Bearer clinic-key-1

{"jsonrpc":"2.0","id":1,"method":"initialize"}
{"jsonrpc":"2.0","id":2,"method":"tools/list"}
{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{
  "name":"search_directory",
  "arguments":{"region":"IN-WB","language":"hi","budget":"low_cost"}}}
```

A runnable minimal client:

```bash
MCP_API_KEYS=clinic-key-1 python client_demo.py \
  --region IN-WB --language hi --budget low_cost
```

### Auth and rate-limit behaviour (what a client sees)

* **No / wrong token** → HTTP `401`, JSON-RPC error `-32001`.
* **Over the per-key limit** → HTTP `429` with `Retry-After`,
  JSON-RPC error `-32002`.
* **Unknown tool** → JSON-RPC error `-32003` ("this server is
  read-only and has no such tool") — the allow-list is enforced
  server-side, not by client etiquette.

## Safety notes for operators

* This is a **directory**, not a crisis line. Clients should
  not present it as one, and Manovia's own crisis flow always
  outranks directory content.
* Every entry names its `source_url` and `last_verified` date;
  a client should display both. Re-verify before relying on a
  number (AGENTS.md safety rule 6).
* The data is curated, not live. If an entry's `needs` or hours
  drift, the fix is a reviewed edit to `directory.json` plus a
  new `last_verified` date — not a runtime fetch.
