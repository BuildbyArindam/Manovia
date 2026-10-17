"""Manovia support directory as a read-only MCP server.

A *separate, optional* module (Day 44 brief §5): another
AI client — a clinic's own assistant, a partner
helpline bot, an operator's tooling — can discover and
search Manovia's curated, verified support directory
over the Model Context Protocol. It can do nothing
else: the server exposes exactly two read-only tools
and has no code path that writes, books, calls or
sends anything.

Transport: JSON-RPC 2.0 over HTTP (``POST /mcp``),
the streamable-HTTP shape MCP clients speak.

Security, both required by the brief:

* **Authentication** — a bearer token per client,
  configured in ``MCP_API_KEYS`` (comma-separated).
  Compared in constant time. No token, no tools.
* **Rate limiting** — a token bucket per key,
  ``MCP_RATE_LIMIT_PER_MINUTE`` (default 60). Over
  the limit is HTTP 429 with ``Retry-After``.

The directory file is read at startup and validated
against the same JSON schema the backend uses
(``../backend/app/content/resources/directory.schema.json``).
Entries marked ``needs_verification`` are excluded
from search results here too, exactly as they are
inside Manovia.

Run it:

    MCP_API_KEYS=clinic-key-1 mcp-server/server.py
    # or: uvicorn --app-dir mcp-server server:app

Then, from any MCP-capable client:

    POST /mcp  Authorization: Bearer clinic-key-1
    {"jsonrpc":"2.0","id":1,"method":"tools/list"}
    {"jsonrpc":"2.0","id":2,"method":"tools/call",
     "params":{"name":"search_directory",
               "arguments":{"region":"IN-WB","language":"hi",
                            "budget":"low_cost"}}}

See README.md in this directory for the full walkthrough.
"""

from __future__ import annotations

import argparse
import hmac
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from jsonschema import Draft202012Validator
from pydantic import BaseModel, Field, model_validator

LOG = logging.getLogger("manovia-mcp")

#: Where the directory lives by default: the backend's
#: shipped copy, so both surfaces read one file.
_DEFAULT_DIRECTORY = (
    Path(__file__).resolve().parent.parent
    / "backend"
    / "app"
    / "content"
    / "resources"
    / "directory.json"
)
_DEFAULT_SCHEMA = (
    Path(__file__).resolve().parent.parent
    / "backend"
    / "app"
    / "content"
    / "resources"
    / "directory.schema.json"
)

#: One search returns at most this many entries.
SEARCH_LIMIT = 8

#: JSON-RPC 2.0 error codes used here.
ERROR_INVALID_REQUEST = -32600
ERROR_METHOD_NOT_FOUND = -32601
ERROR_INVALID_PARAMS = -32602
ERROR_UNAUTHORIZED = -32001
ERROR_RATE_LIMITED = -32002
ERROR_READ_ONLY = -32003


# --- directory -----------------------------------------------------------


class Entry(BaseModel):
    """One directory entry, as this server serves it."""

    id: str
    name: str
    kind: str
    regions: list[str]
    description: str
    contact: dict[str, Any] = Field(default_factory=dict)
    languages: list[str]
    multilingual: bool = False
    cost: str
    hours: str
    needs: list[str]
    source_url: str
    last_verified: str

    @model_validator(mode="before")
    @classmethod
    def _only_verified(cls, data: Any) -> Any:
        return data


class Directory:
    """The curated directory, loaded once, searched read-only."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.entries = [
            Entry(**resource)
            for resource in payload["resources"]
            if resource.get("verification_status") == "verified"
        ]
        self.regions = list(payload.get("regions", []))
        self.excluded_count = sum(
            1
            for resource in payload["resources"]
            if resource.get("verification_status") != "verified"
        )

    @classmethod
    def load(cls, path: Path | None = None) -> Directory:
        file = path or _DEFAULT_DIRECTORY
        payload = json.loads(file.read_text(encoding="utf-8"))
        schema = json.loads(_DEFAULT_SCHEMA.read_text(encoding="utf-8"))
        errors = sorted(
            Draft202012Validator(schema).iter_errors(payload), key=str
        )
        if errors:
            first = errors[0]
            location = "/".join(str(part) for part in first.absolute_path)
            raise ValueError(
                f"directory.json fails its schema at {location}: "
                f"{first.message}"
            )
        return cls(payload)

    def search(
        self,
        *,
        region: str | None = None,
        language: str | None = None,
        need: str | None = None,
        budget: str | None = None,
    ) -> list[Entry]:
        wanted = self._expand_region(region)
        matches: list[tuple[int, Entry]] = []
        for entry in self.entries:
            if wanted is not None and not (
                wanted & set(entry.regions) or "GLOBAL" in entry.regions
            ):
                continue
            if language is not None and not (
                language in entry.languages or entry.multilingual
            ):
                continue
            if need is not None and need != "any" and not (
                need in entry.needs or "any" in entry.needs
            ):
                continue
            if budget == "free" and entry.cost != "free":
                continue
            if budget == "low_cost" and entry.cost not in {
                "free",
                "low_cost",
            }:
                continue
            specificity = 0 if "GLOBAL" in entry.regions else 1
            matches.append((specificity, entry))
        matches.sort(key=lambda pair: (-pair[0], pair[1].name))
        return [entry for _, entry in matches[:SEARCH_LIMIT]]

    @staticmethod
    def _expand_region(region: str | None) -> set[str] | None:
        if region is None:
            return None
        candidates = {region}
        if "-" in region:
            candidates.add(region.split("-", 1)[0])
        return candidates


# --- auth and rate limiting ----------------------------------------------


class TokenBucket:
    """A classic token bucket: ``rate`` tokens per minute."""

    def __init__(self, rate: int) -> None:
        self.rate = rate
        self.tokens = float(rate)
        self.updated = time.monotonic()

    def take(self) -> bool:
        now = time.monotonic()
        elapsed = now - self.updated
        self.updated = now
        self.tokens = min(
            float(self.rate), self.tokens + elapsed * (self.rate / 60.0)
        )
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False

    @property
    def retry_after(self) -> int:
        """Whole seconds until one token is back."""
        missing = 1.0 - self.tokens
        return max(1, int(missing * 60.0 / self.rate) + 1)


class Auth:
    """Bearer-token auth with constant-time comparison."""

    def __init__(self, keys: tuple[str, ...]) -> None:
        self._accepted = keys

    def check(self, presented: str | None) -> bool:
        if presented is None:
            return False
        return any(
            hmac.compare_digest(presented.encode(), key.encode())
            for key in self._accepted
        )


class RateLimiter:
    """Per-key token buckets."""

    def __init__(self, rate_per_minute: int) -> None:
        self._rate = rate_per_minute
        self._buckets: dict[str, TokenBucket] = {}

    def allow(self, key: str) -> tuple[bool, int]:
        bucket = self._buckets.setdefault(key, TokenBucket(self._rate))
        if bucket.take():
            return True, 0
        return False, bucket.retry_after


# --- the MCP surface ------------------------------------------------------

TOOLS: dict[str, dict[str, Any]] = {
    "search_directory": {
        "name": "search_directory",
        "description": (
            "Search Manovia's curated directory of verified "
            "support options (national helplines, campus "
            "counselling, free/low-cost therapy directories, "
            "support groups) by region, language, need and "
            "budget. Read-only."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "region": {
                    "type": "string",
                    "description": "e.g. IN, IN-WB, US, GB, GLOBAL.",
                },
                "language": {
                    "type": "string",
                    "description": "BCP-47-ish code: en, hi, bn, es…",
                },
                "need": {
                    "type": "string",
                    "description": "What kind of support is needed.",
                },
                "budget": {
                    "type": "string",
                    "enum": ["free", "low_cost", "any"],
                },
            },
            "additionalProperties": False,
        },
    },
    "list_regions": {
        "name": "list_regions",
        "description": (
            "List the region codes the directory covers, and how "
            "many verified entries each has. Read-only."
        ),
        "inputSchema": {"type": "object", "properties": {},
                        "additionalProperties": False},
    },
}

#: Every tool is read-only; the server has no other verbs.
READ_ONLY_TOOLS = frozenset(TOOLS)

app = FastAPI(
    title="Manovia support directory (MCP)",
    version="1.0.0",
)

_state: dict[str, Any] = {
    "directory": None,
    "auth": None,
    "limiter": None,
    "initialised": False,
}


def _configure(directory_path: Path | None, keys: tuple[str, ...],
               rate: int) -> None:
    _state["directory"] = Directory.load(directory_path)
    _state["auth"] = Auth(keys)
    _state["limiter"] = RateLimiter(rate)
    _state["initialised"] = True


def _jsonrpc(id_: Any, result: Any) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": id_, "result": result})


def _jsonrpc_error(id_: Any, code: int, message: str) -> JSONResponse:
    return JSONResponse(
        {"jsonrpc": "2.0", "id": id_,
         "error": {"code": code, "message": message}}
    )


@app.post("/mcp")
async def mcp_endpoint(request: Request) -> Response:
    """One endpoint: authenticate, rate-limit, dispatch."""
    body = await request.json()
    id_ = body.get("id")

    auth_header = request.headers.get("Authorization", "")
    token = auth_header.removeprefix("Bearer ").strip()
    auth: Auth = _state["auth"]
    if not auth.check(token):
        return JSONResponse(
            status_code=401,
            content={
                "jsonrpc": "2.0",
                "id": id_,
                "error": {
                    "code": ERROR_UNAUTHORIZED,
                    "message": "A valid MCP_API_KEY bearer token is required.",
                },
            },
        )

    limiter: RateLimiter = _state["limiter"]
    allowed, retry_after = limiter.allow(token)
    if not allowed:
        return JSONResponse(
            status_code=429,
            headers={"Retry-After": str(retry_after)},
            content={
                "jsonrpc": "2.0",
                "id": id_,
                "error": {
                    "code": ERROR_RATE_LIMITED,
                    "message": "Rate limit exceeded; retry later.",
                },
            },
        )

    method = body.get("method")
    params = body.get("params") or {}

    if method == "initialize":
        return _jsonrpc(
            id_,
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {
                    "name": "manovia-support-directory",
                    "version": "1.0.0",
                },
            },
        )
    if method == "ping":
        return _jsonrpc(id_, {})
    if method == "tools/list":
        return _jsonrpc(id_, {"tools": list(TOOLS.values())})
    if method == "tools/call":
        return await _call_tool(id_, params)
    return _jsonrpc_error(
        id_, ERROR_METHOD_NOT_FOUND, f"Unknown method {method!r}."
    )


async def _call_tool(id_: Any, params: dict[str, Any]) -> JSONResponse:
    name = params.get("name")
    arguments = params.get("arguments") or {}
    if name not in READ_ONLY_TOOLS:
        return _jsonrpc_error(
            id_,
            ERROR_READ_ONLY,
            "This server is read-only and has no such tool.",
        )
    directory: Directory = _state["directory"]
    if name == "list_regions":
        counts: dict[str, int] = {}
        for entry in directory.entries:
            for region in entry.regions:
                counts[region] = counts.get(region, 0) + 1
        return _jsonrpc(
            id_,
            {
                "regions": sorted(counts),
                "counts": counts,
                "excluded_needs_verification": directory.excluded_count,
            },
        )
    # search_directory
    for key in ("region", "language", "need", "budget"):
        if key in arguments and not isinstance(arguments[key], str):
            return _jsonrpc_error(
                id_,
                ERROR_INVALID_PARAMS,
                f"{key} must be a string.",
            )
    if arguments.get("budget") not in {None, "free", "low_cost", "any"}:
        return _jsonrpc_error(
            id_,
            ERROR_INVALID_PARAMS,
            "budget must be free, low_cost or any.",
        )
    results = directory.search(
        region=arguments.get("region"),
        language=arguments.get("language"),
        need=arguments.get("need"),
        budget=arguments.get("budget"),
    )
    return _jsonrpc(
        id_,
        {
            "count": len(results),
            "results": [entry.model_dump() for entry in results],
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--directory",
        type=Path,
        default=None,
        help="Path to directory.json (default: the shipped backend copy).",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()

    keys = tuple(
        key.strip()
        for key in os.environ.get("MCP_API_KEYS", "").split(",")
        if key.strip()
    )
    if not keys:
        raise SystemExit("MCP_API_KEYS must name at least one client key")
    rate = int(os.environ.get("MCP_RATE_LIMIT_PER_MINUTE", "60"))
    _configure(args.directory, keys, rate)
    LOG.info(
        "manovia-mcp.starting",
        tools=sorted(READ_ONLY_TOOLS),
        rate_limit_per_minute=rate,
    )
    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
