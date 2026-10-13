# ADR 0027: Generated API reference with a drift test

- Status: accepted (Day 28)
- Date: 2026-10-13
- Deciders: Manovia maintainers
- Supersedes: none
- Related: [0002 backend conventions](0002-backend-skeleton-conventions.md), [0004 authentication and consent](0004-authentication-and-consent.md), [docs/api.md](../api.md)

## Context

The API had no reference document. Route summaries lived only in the FastAPI
code, and a hand-written table would drift from the code after the next change.
Day 28 needed a reference that a reviewer or integrator can read without running
the app, and that cannot quietly disagree with the server.

## Decision

- `backend/scripts/export_openapi.py` builds the app from explicit settings (not
  the environment) and writes two committed files from the same schema:
  `docs/openapi.json` and `docs/api.md`. Nobody edits either file by hand.
- `docs/api.md` has one table per tag. Each row gives the method, path, summary,
  whether a signed-in session is needed, and the status codes the operation
  declares. The session column is read from each route's dependencies
  (`_requires_session` in the script). An operation whose requirement cannot be
  read shows `unknown` rather than being guessed. The Day 28 export has none.
- Mounted routers are walked with FastAPI's `effective_route_contexts()`. In the
  pinned FastAPI version, `app.routes` holds included-router objects, not flat
  route objects, so a flat scan finds nothing.
- The reference documents the **development** surface, so the dev routes under
  `/api/v1/dev` appear and are labelled development-only. A production build does
  not register them. `/metrics` is excluded from the schema.
- `python scripts/export_openapi.py --check` exits 1 if either file is stale.
  `backend/tests/unit/test_api_docs_export.py` (nine tests) runs the same check
  and also confirms that every operation is documented.
- The health router is tagged `health`, so it no longer appears under "Other".
  This is the only application-code change in the Day 28 work.

## Consequences

- Any route change that is not regenerated fails the test suite. The cost is one
  command after an API change.
- The reference describes the **served** behaviour where the schema is wrong, and
  says so in prose:
  - The OpenAPI document declares no `securitySchemes`, so Bearer authentication
    is absent from the generated schema. `docs/api.md` states it in prose. The
    schema fix is parked.
  - Validation failures (422) are served as the project's error envelope
    `{code: "validation_error", message, request_id}`, while the schema lists
    FastAPI's `HTTPValidationError`. `docs/api.md` describes the served shape.
- Refresh tokens are in the JSON body, not a cookie. `docs/api.md` says so, and
  the open item for moving them out of client storage stays in PROGRESS.

## Alternatives considered

- **Hand-written API document.** Rejected: it would drift on the first change,
  and nothing would catch it.
- **Interactive docs only (`/docs`).** Not sufficient on its own: it cannot be
  read in a diff, reviewed in a pull request, or linked as a file from the README.
- **Fix the schema first** (add security schemes and the 422 shape). Deferred:
  it changes a served contract, and it is a code change outside the documentation
  scope.

## Verification

- `python scripts/export_openapi.py --check` reports "api docs up to date".
- The nine tests in `test_api_docs_export.py` pass, and so do the health and
  OpenAPI integration tests. The tests include one that checks the session
  requirement is derived from the routes, and one that checks `/metrics` is kept
  out of the published reference.
- Lint, format and mypy are clean on the three changed Python files, and on the
  whole backend in the Day 28 verification run.
