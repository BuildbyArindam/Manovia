"""Export the API reference from the live FastAPI app (Day 28).

Two committed artifacts are written from the same schema, and nothing in them is
written by hand:

* ``docs/openapi.json`` — the OpenAPI 3.1 document, keys sorted, two-space indent.
* ``docs/api.md`` — a human-readable reference: one table per tag, with the HTTP
  method, path, summary, whether a signed-in session is required, and the status
  codes the operation declares.

Usage (from ``backend/``)::

    python scripts/export_openapi.py          # rewrite both files
    python scripts/export_openapi.py --check  # exit 1 if either file is stale

The export builds the app with explicit settings, not the environment, so the
output does not depend on a developer's ``.env``. It uses the **development**
surface: the dev-only routes under ``/api/v1/dev`` are included and labelled, so
the reference shows every route a development stack serves. A production build
does not register them (see ``app/main.py``).

``backend/tests/unit/test_api_docs_export.py`` fails when the committed files drift
from what this script produces.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = BACKEND_DIR.parent
DOCS_DIR = REPO_DIR / "docs"
OPENAPI_PATH = DOCS_DIR / "openapi.json"
MARKDOWN_PATH = DOCS_DIR / "api.md"

#: Human titles for tags, in the order they appear in the reference.
TAG_TITLES: dict[str, str] = {
    "health": "Health and readiness",
    "auth": "Authentication",
    "consent": "Consent",
    "chat": "Chat",
    "knowledge": "Knowledge base",
    "mood": "Mood check-ins",
    "exercises": "Guided exercises",
    "journal": "Journal",
    "cbt": "CBT thought record",
    "assessments": "Screenings (PHQ-9 and GAD-7)",
    "insights": "Insights",
    "crisis": "Crisis resources and assessment",
    "plan": "Daily plan",
    "settings": "Settings",
    "privacy": "Privacy, export and deletion",
    "dev": "Development only (not in production)",
}

HTTP_METHODS: tuple[str, ...] = ("get", "put", "post", "delete", "patch", "options", "head")


def build_app() -> Any:
    """Build the app with explicit settings and no database connection.

    The settings are fixed here so that the export never reads a ``.env`` file or
    the caller's environment. Nothing is connected: the schema is built from the
    routes alone.
    """
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))
    from app.core.config import Settings
    from app.main import create_app

    settings = Settings(
        _env_file=None,
        app_env="development",
        database_url="sqlite+aiosqlite:///:memory:",
        llm_provider="fake",
        emotion_analyzer="keyword",
        safety_ml_enabled=False,
    )
    return create_app(settings)


def _requires_session(route: Any) -> str:
    """Return ``yes``, ``yes + consent`` or ``no`` for an APIRoute.

    Walks the route's dependency tree. ``get_current_user`` is the bearer-token
    check; ``require_consent(...)`` wraps it and adds a consent gate.
    """
    from app.api.deps import get_current_user

    found = False
    consent = False
    stack = list(route.dependant.dependencies)
    while stack:
        dependency = stack.pop()
        call = dependency.call
        if call is get_current_user:
            found = True
        qualname = getattr(call, "__qualname__", "")
        if qualname.startswith("require_consent"):
            consent = True
            found = True
        stack.extend(dependency.dependencies)
    if not found:
        return "no"
    return "yes + consent" if consent else "yes"


def _session_map(app: Any) -> dict[tuple[str, str], str]:
    """Map ``(path, lowercase method)`` to the session requirement, for every schema route.

    FastAPI 0.143 nests included routers, so the flat ``app.routes`` list holds
    ``_IncludedRouter`` objects. ``effective_route_contexts()`` (the pinned FastAPI
    version exposes it) yields each route with its full prefixed path. Routes that
    are left out of the schema (``include_in_schema=False``, such as ``/metrics``)
    are skipped, so the table matches ``openapi.json`` exactly.
    """
    from fastapi.routing import APIRoute

    contexts: list[tuple[str, Any, APIRoute]] = []
    for route in app.routes:
        if hasattr(route, "effective_route_contexts"):
            for context in route.effective_route_contexts():
                original = context.original_route
                if isinstance(original, APIRoute):
                    contexts.append((context.path, context.methods or set(), original))
        elif isinstance(route, APIRoute):
            contexts.append((route.path, route.methods, route))

    mapping: dict[tuple[str, str], str] = {}
    for path, methods, original in contexts:
        if not original.include_in_schema:
            continue
        for method in sorted(methods):
            mapping[(path, method.lower())] = _requires_session(original)
    return mapping


def build_schema() -> dict[str, Any]:
    """The OpenAPI document, exactly as FastAPI serves it at ``/openapi.json``."""
    app = build_app()
    schema: dict[str, Any] = app.openapi()
    return schema


def _first_sentence(text: str | None) -> str:
    if not text:
        return ""
    first_paragraph = text.strip().split("\n\n", 1)[0]
    flat = " ".join(line.strip() for line in first_paragraph.splitlines())
    return flat.replace("|", "\\|")


def _tag_order(schema: dict[str, Any]) -> list[str]:
    seen: list[str] = []
    for path_item in schema["paths"].values():
        for method in HTTP_METHODS:
            operation = path_item.get(method)
            if not operation:
                continue
            for tag in operation.get("tags", ["other"]):
                if tag not in seen:
                    seen.append(tag)
    ordered = [tag for tag in TAG_TITLES if tag in seen]
    ordered.extend(tag for tag in seen if tag not in TAG_TITLES)
    return ordered


def render_markdown(schema: dict[str, Any], sessions: dict[tuple[str, str], str]) -> str:
    """Render the reference. Output depends only on the schema and the session map."""
    info = schema["info"]
    paths: dict[str, Any] = schema["paths"]
    operation_count = sum(1 for item in paths.values() for method in HTTP_METHODS if method in item)

    lines: list[str] = [
        "# API reference",
        "",
        "> **Generated file — do not edit by hand.** Run "
        "`python backend/scripts/export_openapi.py` from the repository root "
        "(or `cd backend && python scripts/export_openapi.py`) after changing a route. "
        "A test fails when this file is out of date.",
        "",
        f"{info['title']} **{info['version']}** · {operation_count} operations across "
        f"{len(paths)} paths. The machine-readable schema is "
        "[`docs/openapi.json`](openapi.json). The same schema is served live at "
        "`/openapi.json`, with interactive docs at `/docs`, when the API runs.",
        "",
        "## Conventions",
        "",
        "- **Base path.** Every route is under `/api/v1`, except the Prometheus scrape "
        "endpoint `/metrics`, which is served at the root when metrics are enabled.",
        "- **Format.** JSON in and out. Streaming replies use `text/event-stream` "
        "(see the chat section).",
        "- **Sessions.** A signed-in request sends `Authorization: Bearer <access token>`. "
        "Tokens come from `POST /api/v1/auth/guest`, `POST /api/v1/auth/login` or "
        "`POST /api/v1/auth/register`. The **Session** column says where the bearer token "
        "is required. `yes + consent` also needs the named consent before the route runs.",
        "- **Refresh.** `POST /api/v1/auth/refresh` takes the refresh token in the JSON body "
        "and rotates it. Refresh tokens are not set as cookies. See "
        "[PROGRESS.md](../PROGRESS.md) for the open item on moving them out of client storage.",
        "- **Errors.** Every error returns one envelope: "
        '`{"code": "...", "message": "...", "request_id": "..."}`. '
        "Validation failures (`422`) use the same envelope with `code: validation_error`. "
        "The `422` schema shown in the tables below is FastAPI's default, which the server "
        "does not return.",
        "- **Privacy of the body.** Request bodies are not echoed in errors, and the logs carry "
        "no message text (see [docs/privacy-and-threat-model.md](privacy-and-threat-model.md)).",
        "",
        "## Known documentation gaps",
        "",
        "- The OpenAPI document does **not** declare a security scheme, so the bearer "
        "requirement appears only in the **Session** column and in this prose.",
        "- There is no GET streaming route. A GET would carry the user's message in the "
        "query string, so the route was removed on Day 28. Streaming uses POST only.",
        "",
        "## Contents",
        "",
    ]

    tags = _tag_order(schema)
    for tag in tags:
        title = TAG_TITLES.get(tag, tag.title())
        anchor = title.lower().replace(" ", "-").replace("(", "").replace(")", "")
        anchor = anchor.replace(",", "").replace("—", "").replace("--", "-")
        lines.append(f"- [{title}](#{anchor})")
    lines.append("")

    for tag in tags:
        title = TAG_TITLES.get(tag, tag.title())
        lines.append(f"## {title}")
        lines.append("")
        if tag == "dev":
            lines.append(
                "These routes exist only when `APP_ENV` is not `production`. They are "
                "for local debugging and must not be reachable from a deployed API."
            )
            lines.append("")
        lines.append("| Method | Path | Summary | Session | Status codes | Notes |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for path, path_item in paths.items():
            for method in HTTP_METHODS:
                operation = path_item.get(method)
                if not operation or tag not in operation.get("tags", ["other"]):
                    continue
                session = sessions.get((path, method), "unknown")
                codes = ", ".join(sorted(operation.get("responses", {}).keys()))
                summary = (operation.get("summary") or "").replace("|", "\\|")
                notes = _first_sentence(operation.get("description"))
                lines.append(
                    f"| `{method.upper()}` | `{path}` | {summary} | {session} | {codes} | {notes} |"
                )
        lines.append("")

    lines.append("## Regenerating")
    lines.append("")
    lines.append(
        "```bash\ncd backend\n"
        "python scripts/export_openapi.py          # rewrite docs/openapi.json and docs/api.md\n"
        "python scripts/export_openapi.py --check  # staleness check, exits 1 if out of date\n"
        "```"
    )
    lines.append("")
    return "\n".join(lines)


def render_json(schema: dict[str, Any]) -> str:
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def generate() -> tuple[str, str]:
    """Return ``(openapi_json_text, api_markdown_text)`` for the current code."""
    app = build_app()
    schema: dict[str, Any] = app.openapi()
    sessions = _session_map(app)
    return render_json(schema), render_markdown(schema, sessions)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export the Manovia API reference.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="do not write; exit 1 if docs/openapi.json or docs/api.md is out of date",
    )
    args = parser.parse_args(argv)

    json_text, markdown_text = generate()
    if args.check:
        stale = []
        if not OPENAPI_PATH.exists() or OPENAPI_PATH.read_text(encoding="utf-8") != json_text:
            stale.append(str(OPENAPI_PATH.relative_to(REPO_DIR)))
        if not MARKDOWN_PATH.exists() or MARKDOWN_PATH.read_text(encoding="utf-8") != markdown_text:
            stale.append(str(MARKDOWN_PATH.relative_to(REPO_DIR)))
        if stale:
            print("stale: " + ", ".join(stale) + " — run backend/scripts/export_openapi.py")
            return 1
        print("api docs up to date")
        return 0

    OPENAPI_PATH.parent.mkdir(parents=True, exist_ok=True)
    OPENAPI_PATH.write_text(json_text, encoding="utf-8")
    MARKDOWN_PATH.write_text(markdown_text, encoding="utf-8")
    print(f"wrote {OPENAPI_PATH.relative_to(REPO_DIR)} and {MARKDOWN_PATH.relative_to(REPO_DIR)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
