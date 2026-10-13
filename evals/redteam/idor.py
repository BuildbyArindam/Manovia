"""IDOR sweep: two users, every endpoint that takes a resource id.

User **A** creates one of everything. User **B** then calls every id-bearing
route with A's ids, and with a random id that does not exist. Three things are
checked per route:

1. **A** gets the success status (the route works — otherwise "404 for B" would
   prove nothing).
2. **B** gets a 404 for A's id (or the documented status for the route).
3. **B**'s error for A's id is byte-for-byte the same error *code* as for an id
   that does not exist, so the response cannot be used to enumerate ids.

Destructive routes (DELETE, done/skip) are called by B *before* A, so a missing
check would show up as A's later call failing too.

Run it on its own for the Day 29 matrix:

    python evals/redteam/idor.py            # Markdown matrix, exit 1 on any mismatch
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import App, Person, running_app


@dataclass
class IdorRow:
    resource: str
    method: str
    route: str
    owner_expected: int
    owner_actual: int
    other_expected: int
    other_actual: int
    missing_actual: int
    #: Error code B saw for A's id / for a random id (must be equal).
    other_code: str | None
    missing_code: str | None

    @property
    def ok(self) -> bool:
        return (
            self.owner_actual == self.owner_expected
            and self.other_actual == self.other_expected
            and (self.other_expected != 404 or self.other_code == self.missing_code)
        )


def _code(response: Any) -> str | None:
    try:
        return str(response.json()["error"]["code"])
    except Exception:
        return None


async def _seed(app: App, a: Person) -> dict[str, Any]:
    """Create one of every resource for A; return the ids."""
    c, h = app.client, a.headers
    ids: dict[str, Any] = {}

    r = await c.post("/api/v1/chat/sessions", headers=h, json={"save_history": True})
    r.raise_for_status()
    ids["chat_saved"] = r.json()["id"]
    r = await c.post(
        f"/api/v1/chat/sessions/{ids['chat_saved']}/messages",
        headers=h,
        json={"message": "SENTINEL-A-CHAT I had a long day"},
    )
    r.raise_for_status()
    r = await c.post("/api/v1/chat/sessions", headers=h, json={})
    r.raise_for_status()
    ids["chat_ephemeral"] = r.json()["id"]

    r = await c.post(
        "/api/v1/journal",
        headers=h,
        json={"title": "SENTINEL-A-JOURNAL", "body": "A private note about my week."},
    )
    r.raise_for_status()
    ids["journal"] = r.json()["id"]

    r = await c.post(
        "/api/v1/mood", headers=h, json={"valence": 3, "energy": 3, "note": "SENTINEL-A-MOOD"}
    )
    r.raise_for_status()
    ids["mood"] = r.json()["entry"]["id"]

    answers = {f"phq9_{i}": 0 for i in range(1, 10)}
    r = await c.post("/api/v1/assessments/phq9/submit", headers=h, json={"answers": answers})
    r.raise_for_status()
    body = r.json()
    ids["assessment"] = body["result"]["id"]

    record = {
        "situation": "SENTINEL-A-CBT a meeting",
        "automatic_thought": "they think I am slow",
        "evidence_for": "I paused once",
        "evidence_against": "my manager thanked me",
        "balanced_thought": "one pause is normal",
        "emotion": "anxiety",
        "intensity_before": 60,
        "intensity_after": 30,
        "what_helped": [],
    }
    r = await c.post("/api/v1/cbt/records", headers=h, json=record)
    r.raise_for_status()
    ids["cbt"] = r.json()["record"]["id"]

    r = await c.get("/api/v1/plan/today", headers=h)
    r.raise_for_status()
    items = r.json()["items"]
    ids["plan_items"] = [item["id"] for item in items]

    completion = str(uuid.uuid4())
    r = await c.post(
        "/api/v1/exercises/box-breathing/complete",
        headers=h,
        json={"completion_id": completion},
    )
    r.raise_for_status()
    ids["completion"] = completion
    return ids


async def sweep(app: App) -> tuple[list[IdorRow], dict[str, Any]]:
    """Run the sweep on a running app. Returns the matrix and side findings."""
    a = await app.guest("A")
    b = await app.guest("B")
    ids = await _seed(app, a)
    c = app.client
    rows: list[IdorRow] = []
    missing = str(uuid.uuid4())

    async def probe(
        resource: str,
        method: str,
        route: str,
        rid: str,
        *,
        owner_expected: int,
        other_expected: int = 404,
        json_body: Any = None,
        owner_first: bool = True,
    ) -> None:
        path = route.format(id=rid)
        missing_path = route.format(id=missing)

        async def call(person: Person, url: str) -> Any:
            return await c.request(method, url, headers=person.headers, json=json_body)

        if owner_first:
            owner = await call(a, path)
            other = await call(b, path)
        else:  # destructive: the attacker goes first
            other = await call(b, path)
            owner = await call(a, path)
        missing_resp = await call(b, missing_path)
        rows.append(
            IdorRow(
                resource=resource,
                method=method,
                route=route.replace("{id}", "{" + resource + "_id}"),
                owner_expected=owner_expected,
                owner_actual=owner.status_code,
                other_expected=other_expected,
                other_actual=other.status_code,
                missing_actual=missing_resp.status_code,
                other_code=_code(other),
                missing_code=_code(missing_resp),
            )
        )

    msg = {"message": "hello again"}
    for kind in ("chat_saved", "chat_ephemeral"):
        sid = ids[kind]
        await probe(kind, "GET", "/api/v1/chat/sessions/{id}", sid, owner_expected=200)
        await probe(kind, "GET", "/api/v1/chat/sessions/{id}/messages", sid, owner_expected=200)
        await probe(
            kind,
            "POST",
            "/api/v1/chat/sessions/{id}/messages",
            sid,
            owner_expected=200,
            json_body=msg,
        )
        await probe(
            kind,
            "POST",
            "/api/v1/chat/sessions/{id}/stream",
            sid,
            owner_expected=200,
            json_body=msg,
        )

    jid = ids["journal"]
    await probe("journal", "GET", "/api/v1/journal/{id}", jid, owner_expected=200)
    await probe(
        "journal",
        "PUT",
        "/api/v1/journal/{id}",
        jid,
        owner_expected=200,
        json_body={"title": "edited", "body": "edited body"},
        owner_first=False,
    )
    await probe(
        "journal", "DELETE", "/api/v1/journal/{id}", jid, owner_expected=204, owner_first=False
    )

    await probe(
        "mood", "DELETE", "/api/v1/mood/{id}", ids["mood"], owner_expected=200, owner_first=False
    )

    await probe(
        "assessment",
        "GET",
        "/api/v1/assessments/phq9/history/{id}",
        ids["assessment"],
        owner_expected=200,
    )

    await probe("cbt", "GET", "/api/v1/cbt/records/{id}", ids["cbt"], owner_expected=200)
    await probe(
        "cbt",
        "DELETE",
        "/api/v1/cbt/records/{id}",
        ids["cbt"],
        owner_expected=204,
        owner_first=False,
    )

    if ids["plan_items"]:
        item = ids["plan_items"][0]
        # skip first (attacker first), then done on the same item: both are
        # state changes the owner may make in either order.
        await probe(
            "plan_item",
            "POST",
            "/api/v1/plan/{id}/skip",
            item,
            owner_expected=200,
            owner_first=False,
        )
        await probe(
            "plan_item",
            "POST",
            "/api/v1/plan/{id}/done",
            item,
            owner_expected=200,
            owner_first=False,
        )

    # A's PHQ-9 id through the GAD-7 path must not resolve for A either.
    await probe(
        "assessment_cross_instrument",
        "GET",
        "/api/v1/assessments/gad7/history/{id}",
        ids["assessment"],
        owner_expected=404,
    )

    # Side findings that do not fit the owner/other/missing shape.
    side: dict[str, Any] = {}
    reuse = await c.post(
        "/api/v1/exercises/box-breathing/complete",
        headers=b.headers,
        json={"completion_id": ids["completion"]},
    )
    side["completion_id_reuse_by_other"] = {
        "status": reuse.status_code,
        "code": _code(reuse),
        "leaks_owner_data": "SENTINEL" in reuse.text or str(a.id) in reuse.text,
    }
    export_b = await c.get("/api/v1/privacy/export?format=json", headers=b.headers)
    side["export_of_b_contains_a_data"] = (
        "SENTINEL-A" in export_b.text or str(a.id) in export_b.text
    )
    side["export_of_b_status"] = export_b.status_code
    listings = {
        "journal": "/api/v1/journal",
        "mood": "/api/v1/mood",
        "assessments": "/api/v1/assessments/phq9/history",
        "cbt": "/api/v1/cbt/records",
        "exercise_history": "/api/v1/exercises/history",
        "insights": "/api/v1/insights/weekly",
    }
    leaks: dict[str, Any] = {}
    for name, url in listings.items():
        listing = await c.get(url, headers=b.headers)
        text = listing.text
        leaked_ids = [k for k, v in ids.items() if isinstance(v, str) and v in text]
        leaks[name] = {
            "status": listing.status_code,
            "leaks": "SENTINEL-A" in text or bool(leaked_ids),
        }
    side["listings_of_b"] = leaks
    side["any_listing_leak"] = any(v["leaks"] for v in leaks.values())
    memory_b = await c.get("/api/v1/chat/memory", headers=b.headers)
    side["memory_of_b_contains_a_data"] = "SENTINEL-A" in memory_b.text
    return rows, side


def render_markdown(rows: list[IdorRow], side: dict[str, Any]) -> str:
    lines = [
        "| resource | method | route | A (owner) exp/act | B (other) exp/act | B random id | "
        "same error code | result |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        same = "yes" if row.other_code == row.missing_code else "NO"
        lines.append(
            f"| {row.resource} | {row.method} | `{row.route}` | {row.owner_expected}/"
            f"{row.owner_actual} | {row.other_expected}/{row.other_actual} | "
            f"{row.missing_actual} | {same} (`{row.other_code}`) | "
            f"{'PASS' if row.ok else 'FAIL'} |"
        )
    lines.append("")
    lines.append("Side checks: `" + json.dumps(side, sort_keys=True) + "`")
    return "\n".join(lines)


async def _main() -> int:
    async with running_app() as app:
        rows, side = await sweep(app)
    print(render_markdown(rows, side))
    failed = [asdict(row) for row in rows if not row.ok]
    leaked = (
        side["export_of_b_contains_a_data"]
        or side["memory_of_b_contains_a_data"]
        or side["any_listing_leak"]
    )
    print(
        f"\n{len(rows) - len(failed)}/{len(rows)} routes PASS; "
        f"data leak via listings/export/memory: {leaked}"
    )
    return 1 if failed or leaked else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
