"""A minimal MCP client for the Manovia directory server.

Demonstrates the full handshake from another AI client's
point of view: initialize → tools/list → tools/call, with
the bearer token in the Authorization header. Exits non-zero
on any JSON-RPC error so it can be used as a smoke check.

Usage:
    MCP_API_KEYS=... not needed here — pass the token:
    python client_demo.py --token clinic-key-1 \
        --region IN-WB --language hi --budget low_cost
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

import httpx

DEFAULT_URL = "http://127.0.0.1:8787/mcp"


def call(
    client: httpx.Client,
    method: str,
    params: dict[str, Any] | None = None,
    *,
    id_: int = 1,
) -> dict[str, Any]:
    response = client.post(
        DEFAULT_URL,
        json={
            "jsonrpc": "2.0",
            "id": id_,
            "method": method,
            **({"params": params} if params is not None else {}),
        },
    )
    payload = response.json()
    if response.status_code != 200 or "error" in payload:
        print(
            f"error {response.status_code}: "
            f"{payload.get('error', payload)}",
            file=sys.stderr,
        )
        sys.exit(1)
    return payload["result"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--token", required=True, help="bearer token")
    parser.add_argument("--region", default=None)
    parser.add_argument("--language", default=None)
    parser.add_argument("--need", default=None)
    parser.add_argument(
        "--budget",
        default=None,
        choices=["free", "low_cost", "any"],
    )
    args = parser.parse_args()

    with httpx.Client(
        headers={"Authorization": f"Bearer {args.token}"},
        timeout=10.0,
    ) as client:
        server = call(client, "initialize", id_=1)
        print("server:", server["serverInfo"]["name"])

        tools = call(client, "tools/list", id_=2)
        print("tools:", ", ".join(tool["name"] for tool in tools["tools"]))

        arguments = {
            key: value
            for key, value in (
                ("region", args.region),
                ("language", args.language),
                ("need", args.need),
                ("budget", args.budget),
            )
            if value is not None
        }
        result = call(
            client,
            "tools/call",
            {"name": "search_directory", "arguments": arguments},
            id_=3,
        )
        print(f"matches: {result['count']}")
        for entry in result["results"]:
            contact = entry["contact"]
            channel = contact.get("phone") or contact.get("web") or ""
            print(f"- {entry['name']} ({entry['cost']}, {entry['hours']})")
            print(f"  {channel}")


if __name__ == "__main__":
    main()
