"""Day 15 verification — mood check-ins, run for real against a real database.

This is the evidence script for the Mood check-ins milestone. It is deliberately
not a test: it builds the app in-process against a throwaway SQLite file, drives
the *real* HTTP routes with the *real* rules engine and the *real* field cipher,
reads the database with raw SQL, and prints a PASS/FAIL table at the end. Every
claim in the Day 15 handover that can be checked by a program is checked here.

Run it from the repository root:

    backend/.venv/bin/python scripts/verify_day15.py

or, to also run the frontend unit suite as one of the checks:

    backend/.venv/bin/python scripts/verify_day15.py --frontend

What it checks
--------------

1. **Ten check-ins over several days, with explicit timestamps.** Seeded through
   the repository with ``created_at`` chosen by hand (the API always stamps
   "now"; history is what a range query is for), then read back through
   ``GET /api/v1/mood``.
2. **Range queries.** Date-only bounds are whole local days and ``to`` is
   inclusive; timestamp bounds are exact instants; the same instant set is a
   different set of local days in ``Asia/Kolkata`` than in ``America/New_York``.
3. **The streak, in the caller's timezone.** Midnight in Kolkata, the spring
   DST change and the autumn DST change in New York, a bridged day, and the
   welcome-back state after a gap. The clock is pinned through the documented
   seam (``app.api.v1.mood._now``) so "today" is a fact of the run.
4. **A worrying note.** The HIGH fixture phrase goes in a note: the response
   carries a rendered crisis message and the region's helplines, the check-in is
   still saved, a metadata-only ``safety_events`` row is written, and no part of
   the note exists anywhere in the database except as ciphertext.
5. **Encryption at rest.** The raw ``note_encrypted`` column is read with SQL:
   the plaintext must not appear in it, in any other column, in the safety
   event, or in the response of the *other* account.
6. **Ownership.** Another account cannot list or delete the first account's
   check-ins, and a stranger's id is a 404 — not a 403, which would confirm it
   exists.
7. **The copy.** Every mood string the UI can show is dumped from the frontend
   source by ``frontend/scripts/mood-copy-dump.ts`` and scanned here with an
   independent blocklist, so guilt-inducing or cheerleading wording cannot hide
   behind the frontend's own test.

Exit code is 0 only if every check passes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import secrets
import subprocess
import sys
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select
from sqlalchemy import text as sa_text

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = REPO_ROOT / "backend"
FRONTEND_ROOT = REPO_ROOT / "frontend"
sys.path.insert(0, str(BACKEND_ROOT))

from app.core import crypto  # noqa: E402
from app.core.config import Settings  # noqa: E402
from app.db.repos import MoodRepository  # noqa: E402
from app.db.session import build_database  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Base  # noqa: E402
from app.models.mood import MoodEntry  # noqa: E402
from app.models.safety_event import SafetyEvent  # noqa: E402

MOOD = "/api/v1/mood"
STREAK = "/api/v1/mood/streak"
CONSENT_VERSION = "2026-10-01"

KOLKATA = ZoneInfo("Asia/Kolkata")
NEW_YORK = ZoneInfo("America/New_York")

HIGH_RISK_NOTE = "I want to kill myself"
ORDINARY_NOTE = "Long day, but I made it to the evening."

#: Guilt and cheerleading vocabulary, written here rather than imported from the
#: frontend test on purpose: a second, independent list is the whole point of a
#: verification pass. Phrases are matched case-insensitively as substrings.
GUILT_PHRASES: tuple[str, ...] = (
    "streak broken",
    "you broke",
    "broke your",
    "lost your streak",
    "don't lose",
    "do not lose",
    "keep it up",
    "you missed",
    "you failed",
    "back on track",
    "get back on track",
    "don't give up",
    "try harder",
    "you should have",
    "be consistent",
    "stay disciplined",
    "make it count",
    "no excuses",
    "shame",
    "guilty",
)
#: Words that would turn a feeling into a verdict if they ever reached the
#: vocabulary: the check-in offers people words for themselves, and none of them
#: may be a judgement on how somebody is living.
VOCABULARY_JUDGEMENT_PHRASES: tuple[str, ...] = (
    "lazy",
    "failure",
    "weak",
    "broken",
    "pathetic",
    "unmotivated",
    "excuse",
    "drama",
    "attention",
)
CHEERLEADING_PHRASES: tuple[str, ...] = (
    "you've got this",
    "you got this",
    "amazing",
    "crushing it",
    "so proud",
    "well done",
    "great job",
    "congratulations",
    "awesome",
    "smash it",
    "on fire",
    "keep smiling",
    "be positive",
    "everything happens for a reason",
    "high five",
)


@dataclass
class Check:
    """One verification result, as it will appear in the table."""

    name: str
    passed: bool
    detail: str = ""

    def as_row(self) -> tuple[str, str, str]:
        return (self.name, "PASS" if self.passed else "FAIL", self.detail)


@dataclass
class Report:
    """The collected results, plus a few numbers worth quoting in the handover."""

    checks: list[Check] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def add(self, name: str, passed: bool, detail: str = "") -> None:
        self.checks.append(Check(name, passed, detail))

    def expect(self, name: str, actual: Any, expected: Any) -> None:
        """Record a PASS/FAIL from an equality, keeping the values visible."""
        self.add(name, actual == expected, f"got {actual!r}, expected {expected!r}")

    def note(self, message: str) -> None:
        self.notes.append(message)


# --------------------------------------------------------------------------- #
# App wiring                                                                     #
# --------------------------------------------------------------------------- #


def build_app(db_path: Path) -> tuple[Any, Any]:
    """A real app on a throwaway SQLite file, with the ML classifier off."""
    if db_path.exists():
        db_path.unlink()
    key = crypto.FernetCipher.generate_key()
    settings = Settings(
        _env_file=None,
        app_env="test",
        secret_key=secrets.token_urlsafe(48),
        field_encryption_key=key,
        allowed_origins="http://testserver",
        database_url=f"sqlite:///{db_path}",
        safety_ml_enabled="false",
        llm_provider="fake",
        chat_rate_limit_per_minute=10_000,
        log_level="warning",
    )
    crypto.configure_cipher(crypto.FernetCipher.from_settings(settings))
    database = build_database(settings)
    app = create_app(settings, database=database)
    return app, database


async def create_tables(database: Any) -> None:
    async with database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def make_person(client: httpx.AsyncClient) -> tuple[uuid.UUID, dict[str, str]]:
    """A guest with the agreements a check-in needs (terms + privacy)."""
    guest = await client.post("/api/v1/auth/guest")
    guest.raise_for_status()
    body = guest.json()
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    granted = await client.post(
        "/api/v1/consent",
        headers=headers,
        json={
            "grants": [
                {"kind": kind, "version": CONSENT_VERSION, "granted": True}
                for kind in ("terms", "privacy")
            ]
        },
    )
    assert granted.status_code == 201, granted.text
    return uuid.UUID(body["user"]["id"]), headers


async def seed(
    database: Any,
    user_id: uuid.UUID,
    *,
    at: datetime,
    valence: int = 3,
    energy: int = 3,
    emotions: tuple[str, ...] = (),
    factors: tuple[str, ...] = (),
    note: str | None = None,
) -> uuid.UUID:
    """Insert one check-in with an explicit instant (history, not "now")."""
    async with database.session_factory() as session:
        entry = await MoodRepository(session).record(
            user_id=user_id,
            valence=valence,
            energy=energy,
            emotions=list(emotions),
            factors=dict.fromkeys(factors) if factors else None,
            note=note,
        )
        entry.created_at = at
        await session.commit()
        return entry.id


# --------------------------------------------------------------------------- #
# Checks                                                                         #
# --------------------------------------------------------------------------- #


async def check_range_queries(client: httpx.AsyncClient, database: Any, report: Report) -> None:
    """10 check-ins over 7 local days, then read them back through the API."""
    print("\n[1] Ten check-ins with explicit timestamps, and range queries")

    user_id, headers = await make_person(client)

    # Five consecutive local days in Kolkata, two check-ins on two of the days so
    # "days" and "check-ins" are not the same number anywhere in this script.
    plan: list[tuple[str, datetime, int, int, tuple[str, ...], tuple[str, ...], str | None]] = [
        (
            "2026-03-09 08:10 IST",
            datetime(2026, 3, 9, 2, 40, tzinfo=UTC),
            2,
            3,
            ("sadness",),
            ("work",),
            "Monday.",
        ),
        (
            "2026-03-09 21:30 IST",
            datetime(2026, 3, 9, 16, 0, tzinfo=UTC),
            3,
            2,
            ("calm",),
            (),
            None,
        ),
        (
            "2026-03-10 07:05 IST",
            datetime(2026, 3, 10, 1, 35, tzinfo=UTC),
            2,
            2,
            ("anxiety", "fear"),
            ("sleep", "work"),
            "Slept badly.",
        ),
        (
            "2026-03-11 23:55 IST",
            datetime(2026, 3, 11, 18, 25, tzinfo=UTC),
            4,
            4,
            ("joy",),
            ("friends",),
            "Dinner with friends.",
        ),
        (
            "2026-03-12 00:05 IST",
            datetime(2026, 3, 11, 18, 35, tzinfo=UTC),
            4,
            3,
            (),
            ("family",),
            "Late call home.",
        ),
        (
            "2026-03-12 19:00 IST",
            datetime(2026, 3, 12, 13, 30, tzinfo=UTC),
            3,
            3,
            ("neutral",),
            (),
            None,
        ),
        (
            "2026-03-13 09:15 IST",
            datetime(2026, 3, 13, 3, 45, tzinfo=UTC),
            1,
            1,
            ("sadness", "loneliness"),
            ("health",),
            ORDINARY_NOTE,
        ),
        (
            "2026-03-13 22:40 IST",
            datetime(2026, 3, 13, 17, 10, tzinfo=UTC),
            2,
            2,
            ("fear",),
            ("money",),
            None,
        ),
        (
            "2026-03-14 11:00 IST",
            datetime(2026, 3, 14, 5, 30, tzinfo=UTC),
            5,
            4,
            ("joy", "calm"),
            ("weather",),
            "Sun came out.",
        ),
        (
            "2026-03-15 09:30 IST",
            datetime(2026, 3, 15, 4, 0, tzinfo=UTC),
            3,
            3,
            ("neutral",),
            ("study",),
            None,
        ),
    ]
    seeded_instants = [at for _, at, *_ in plan]
    for label, at, valence, energy, emotions, factors, note in plan:
        await seed(
            database,
            user_id,
            at=at,
            valence=valence,
            energy=energy,
            emotions=emotions,
            factors=factors,
            note=note,
        )
        print(f"    seeded {label} -> valence {valence}, energy {energy}")

    # The whole set, no bounds, counted in the caller's zone.
    response = await client.get(f"{MOOD}?tz=Asia/Kolkata", headers=headers)
    body = response.json()
    report.expect("GET /mood lists all 10 check-ins", body["count"], 10)
    report.expect("newest check-in comes first", body["entries"][0]["local_date"], "2026-03-15")
    report.expect("oldest check-in is last", body["entries"][-1]["local_date"], "2026-03-09")
    report.expect("timezone echoed", body["timezone"], "Asia/Kolkata")
    report.expect("no UTC fallback", body["timezone_fallback_used"], False)

    # The 23:55 and 00:05 pair: one is 11 March, the other 12 March in Kolkata,
    # though they are only ten minutes apart in UTC. That is the whole reason the
    # streak is computed in the caller's zone.
    dates = sorted({entry["local_date"] for entry in body["entries"]})
    report.expect(
        "ten check-ins fall on seven local days",
        dates,
        [
            "2026-03-09",
            "2026-03-10",
            "2026-03-11",
            "2026-03-12",
            "2026-03-13",
            "2026-03-14",
            "2026-03-15",
        ],
    )
    report.add(
        "midnight pair split across two local days",
        "2026-03-11" in dates and "2026-03-12" in dates,
        f"local days: {dates}",
    )

    # Date-only range: 11 March to 13 March inclusive, in Kolkata.
    ranged = await client.get(
        f"{MOOD}?from=2026-03-11&to=2026-03-13&tz=Asia/Kolkata", headers=headers
    )
    ranged_body = ranged.json()
    report.expect("date range 11-13 March returns 5 check-ins", ranged_body["count"], 5)
    report.expect(
        "the `to` day is included",
        "2026-03-13" in {entry["local_date"] for entry in ranged_body["entries"]},
        True,
    )

    # One local day, asked twice. The same instants are a different set of local
    # days in another zone, and which day holds which check-in is computed here
    # from the timestamps rather than trusted to the API under test.
    expected_ist = Counter(at.astimezone(KOLKATA).date() for at in seeded_instants)
    expected_ny = Counter(at.astimezone(NEW_YORK).date() for at in seeded_instants)
    report.add(
        "the same instants partition differently in another zone",
        expected_ist != expected_ny,
        "Kolkata: "
        + ", ".join(f"{day.isoformat()}x{count}" for day, count in sorted(expected_ist.items()))
        + " | New York: "
        + ", ".join(f"{day.isoformat()}x{count}" for day, count in sorted(expected_ny.items())),
    )
    for zone_name, expected in (("Asia/Kolkata", expected_ist), ("America/New_York", expected_ny)):
        mismatches = []
        for day, count in sorted(expected.items()):
            answer = await client.get(
                f"{MOOD}?from={day.isoformat()}&to={day.isoformat()}&tz={zone_name}",
                headers=headers,
            )
            if answer.json()["count"] != count:
                mismatches.append(f"{day} {answer.json()['count']}≠{count}")
        report.add(
            f"the API's local days match the timestamps in {zone_name}",
            not mismatches,
            "; ".join(mismatches)
            or f"{len(expected)} local day(s) all agree ({sum(expected.values())} check-ins)",
        )

    # Timestamp bounds are exact instants, not local days.
    instant = await client.get(
        f"{MOOD}?from=2026-03-11T18:30:00Z&to=2026-03-12T13:30:00Z&tz=Asia/Kolkata",
        headers=headers,
    )
    instant_body = instant.json()
    report.expect("timestamp range is an exact-instant filter", instant_body["count"], 1)

    # A one-sided range, and a limit.
    limited = await client.get(f"{MOOD}?from=2026-03-10&limit=2&tz=Asia/Kolkata", headers=headers)
    limited_body = limited.json()
    report.expect("limit caps the page", limited_body["count"], 2)
    report.expect("limit echoed", limited_body["limit"], 2)

    # Bad ranges are errors, not silent corrections.
    backwards = await client.get(
        f"{MOOD}?from=2026-03-13&to=2026-03-11&tz=Asia/Kolkata", headers=headers
    )
    report.expect("a backwards range is a 422", backwards.status_code, 422)
    report.expect("  with a stable code", backwards.json()["error"]["code"], "invalid_range")

    broken_tz = await client.get(f"{MOOD}?tz=Mars/Olympus_Mons", headers=headers)
    report.expect("an unknown timezone is a 422", broken_tz.status_code, 422)
    report.expect("  with a stable code", broken_tz.json()["error"]["code"], "invalid_timezone")

    report.note(f"10 check-ins across {len(dates)} local days, newest first")


async def check_streak(
    client: httpx.AsyncClient, database: Any, report: Report, monkeypatch: Any
) -> None:
    """The streak across midnight, both DST changes, a bridged day and a gap."""
    print("\n[2] The streak in the caller's timezone")

    user_id, headers = await make_person(client)

    # ---- Asia/Kolkata, pinned to 15 March 2026 (noon UTC = 17:30 IST) --------
    #
    # The live run: 11, 12, 13, (14 missed → freeze), 15. Five days of the run
    # are check-in days; the missed 14th is bridged, not counted as a day.
    for day in (11, 12, 13, 15):
        await seed(
            database,
            user_id,
            at=datetime(2026, 3, day, 9, 0, tzinfo=KOLKATA).astimezone(UTC),
        )

    monkeypatch.setattr("app.api.v1.mood._now", lambda: datetime(2026, 3, 15, 12, 0, tzinfo=UTC))
    streak = await client.get(f"{STREAK}?tz=Asia/Kolkata", headers=headers)
    body = streak.json()
    report.expect("state is today", body["state"], "today")
    report.expect("today counts", body["checked_in_today"], True)
    report.expect("current streak counts check-in days only", body["current_streak"], 4)
    report.expect("one freeze used", body["freezes_used"], 1)
    report.expect("freeze spent recently", body["freeze_available"], False)
    report.expect("longest run is the live run", body["longest_streak"], 4)
    report.expect("timezone is the caller's", body["timezone"], "Asia/Kolkata")

    # ---- Midnight: 23:55 IST and 00:05 IST are two days, ten minutes apart ---
    midnight_user, midnight_headers = await make_person(client)
    await seed(database, midnight_user, at=datetime(2026, 3, 11, 18, 25, tzinfo=UTC))  # 23:55 IST
    await seed(database, midnight_user, at=datetime(2026, 3, 11, 18, 35, tzinfo=UTC))  # 00:05 IST
    monkeypatch.setattr("app.api.v1.mood._now", lambda: datetime(2026, 3, 12, 4, 0, tzinfo=UTC))
    body = (await client.get(f"{STREAK}?tz=Asia/Kolkata", headers=midnight_headers)).json()
    report.expect("midnight: two days across a ten-minute gap", body["current_streak"], 2)
    report.expect("midnight: the 00:05 check-in is today in Kolkata", body["state"], "today")
    report.expect("midnight: no false freeze", body["freezes_used"], 0)

    # UTC-only would have put both on 11 March and would answer 1.
    utc_view = (await client.get(f"{STREAK}", headers=midnight_headers)).json()
    report.add(
        "the same rows are one day in UTC (fallback reported)",
        utc_view["current_streak"] == 1 and utc_view["timezone_fallback_used"] is True,
        (
            f"UTC says {utc_view['current_streak']} day(s), "
            f"fallback={utc_view['timezone_fallback_used']}"
        ),
    )

    # ---- Spring forward, America/New_York: 8 March 2026 is 23 hours long -----
    dst_user, dst_headers = await make_person(client)
    for local_day in (
        datetime(2026, 3, 7, 20, 0),
        datetime(2026, 3, 8, 20, 0),
        datetime(2026, 3, 9, 20, 0),
    ):
        await seed(
            database,
            dst_user,
            at=local_day.replace(tzinfo=NEW_YORK).astimezone(UTC),
        )
    monkeypatch.setattr("app.api.v1.mood._now", lambda: datetime(2026, 3, 9, 23, 30, tzinfo=UTC))
    body = (await client.get(f"{STREAK}?tz=America/New_York", headers=dst_headers)).json()
    report.expect("spring-forward: three consecutive days", body["current_streak"], 3)
    report.expect("spring-forward: no freeze needed", body["freezes_used"], 0)
    report.expect("spring-forward: today counted", body["checked_in_today"], True)

    # ---- Fall back, America/New_York: 1 November 2026 is 25 hours long -------
    fall_user, fall_headers = await make_person(client)
    for local_day in (
        datetime(2026, 10, 31, 22, 30),
        datetime(2026, 11, 1, 22, 30),
        datetime(2026, 11, 2, 22, 30),
    ):
        await seed(database, fall_user, at=local_day.replace(tzinfo=NEW_YORK).astimezone(UTC))
    monkeypatch.setattr("app.api.v1.mood._now", lambda: datetime(2026, 11, 2, 23, 0, tzinfo=UTC))
    body = (await client.get(f"{STREAK}?tz=America/New_York", headers=fall_headers)).json()
    report.expect("fall-back: three consecutive days, none doubled", body["current_streak"], 3)

    # ---- A gap of two days is "returning", with the old numbers kept ---------
    gap_user, gap_headers = await make_person(client)
    for day in (2, 3, 4, 5):
        await seed(
            database, gap_user, at=datetime(2026, 3, day, 9, 0, tzinfo=KOLKATA).astimezone(UTC)
        )
    monkeypatch.setattr("app.api.v1.mood._now", lambda: datetime(2026, 3, 10, 12, 0, tzinfo=UTC))
    body = (await client.get(f"{STREAK}?tz=Asia/Kolkata", headers=gap_headers)).json()
    report.expect("after a gap: returning, not broken", body["state"], "returning")
    report.expect("after a gap: the run count is zero", body["current_streak"], 0)
    report.expect("after a gap: the record is still there", body["longest_streak"], 4)
    report.expect("after a gap: days since the last check-in", body["days_since_last_check_in"], 5)
    report.expect("after a gap: a new start still gets a freeze", body["freeze_available"], True)

    # ---- Nothing yet: a state, not a scold ----------------------------------
    empty_user, empty_headers = await make_person(client)
    body = (await client.get(f"{STREAK}?tz=Asia/Kolkata", headers=empty_headers)).json()
    report.expect("no check-ins: state never", body["state"], "never")
    report.expect("no check-ins: no invented numbers", body["current_streak"], 0)
    assert empty_user  # (the guest exists; only its streak is empty)


async def check_high_risk_note(
    client: httpx.AsyncClient, database: Any, report: Report, monkeypatch: Any
) -> None:
    """A HIGH note: help in the response, the check-in still saved, no text stored."""
    print("\n[3] A worrying note returns crisis help and keeps the check-in")

    user_id, headers = await make_person(client)
    monkeypatch.setattr("app.api.v1.mood._now", lambda: datetime(2026, 3, 15, 12, 0, tzinfo=UTC))

    response = await client.post(
        MOOD,
        headers=headers,
        json={
            "valence": 1,
            "energy": 2,
            "emotions": ["sadness", "fear"],
            "factors": ["sleep"],
            "note": HIGH_RISK_NOTE,
            "tz": "Asia/Kolkata",
            "locale": "en",
        },
    )
    report.expect("POST /mood with a HIGH note is a 201", response.status_code, 201)
    body = response.json()
    crisis = body.get("crisis")
    report.add("the response carries crisis metadata", isinstance(crisis, dict), str(type(crisis)))
    if isinstance(crisis, dict):
        report.expect("  level is high", crisis["level"], "high")
        report.expect("  stored level is crisis", crisis["stored_level"], "crisis")
        report.expect("  it is not about someone else", crisis["about_someone_else"], False)
        report.expect("  check-in saved flag", crisis["check_in_saved"], True)
        message = crisis.get("message")
        report.add(
            "  a pre-written message is included",
            isinstance(message, dict) and bool(message.get("title")),
            (message or {}).get("title", "missing"),
        )
        report.add(
            "  the message is not the note",
            isinstance(message, dict) and HIGH_RISK_NOTE not in json.dumps(message),
            "message contains no fragment of the note",
        )
        resources = crisis.get("resources") or []
        callable_lines = [r for r in resources if r.get("tel_href")]
        report.add(
            "  helplines are included and each one is reachable",
            bool(resources)
            and bool(callable_lines)
            and all(r.get("tel_href") or r.get("url") for r in resources),
            f"{len(resources)} resource(s), {len(callable_lines)} callable: "
            + ", ".join(
                f"{r['name']} ({r.get('tel_href') or r.get('url')})" for r in resources[:3]
            ),
        )
        report.add(
            "  every helpline carries last_verified",
            all(resource.get("last_verified") for resource in resources),
            "last_verified present on all",
        )
        report.add(
            "  rationale codes look like codes, not prose",
            bool(crisis["rationale_codes"])
            and all(" " not in code for code in crisis["rationale_codes"]),
            f"{len(crisis['rationale_codes'])} code(s)",
        )

    # The entry exists, with its note, for its owner.
    listing = (await client.get(f"{MOOD}?tz=Asia/Kolkata", headers=headers)).json()
    report.expect("the check-in was stored anyway", listing["count"], 1)
    report.expect(
        "and its note comes back to its owner", listing["entries"][0]["note"], HIGH_RISK_NOTE
    )
    report.expect("the streak includes it", body["streak"]["current_streak"], 1)

    # A metadata-only audit row, and nothing else.
    async with database.session_factory() as session:
        events = (
            (await session.execute(select(SafetyEvent).where(SafetyEvent.user_id == user_id)))
            .scalars()
            .all()
        )
    report.expect("one safety event was recorded", len(events), 1)
    row = events[0] if events else None
    report.add(
        "the safety event records the tier and the detector",
        row is not None and row.risk_level == 3 and row.source.value in {"rules", "ml"},
        (
            f"risk_level={getattr(row, 'risk_level', None)}, "
            f"source={getattr(getattr(row, 'source', None), 'value', None)}, "
            f"category={getattr(row, 'category', None)}"
        ),
    )
    report.add(
        "the safety event has no place to put text",
        not hasattr(SafetyEvent, "text") and not hasattr(SafetyEvent, "message"),
        f"columns: {sorted(column.name for column in SafetyEvent.__table__.columns)}",
    )

    # No ordinary note is screened into a crisis card.
    calm = await client.post(
        MOOD,
        headers=headers,
        json={
            "valence": 4,
            "energy": 3,
            "emotions": ["calm"],
            "factors": ["weather"],
            "note": ORDINARY_NOTE,
            "tz": "Asia/Kolkata",
        },
    )
    calm_body = calm.json()
    report.add(
        "an ordinary note carries no crisis metadata",
        calm_body.get("crisis") is None,
        f"crisis={calm_body.get('crisis')!r}",
    )

    # The safety engine failing closed: nothing is written when the note cannot
    # be screened (checked separately by the unit suite; asserted here as 503).
    async def explode(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("engine down")

    # Patch the name the mood route actually calls: it was imported into
    # ``app.api.v1.mood``, so patching the pipeline module would miss it.
    monkeypatch.setattr("app.api.v1.mood.run_ensemble", explode)
    failed = await client.post(
        MOOD,
        headers=headers,
        json={"valence": 3, "energy": 3, "note": "A note.", "tz": "Asia/Kolkata"},
    )
    report.expect("an unscreenable note fails closed with 503", failed.status_code, 503)
    report.expect("  with a stable code", failed.json()["error"]["code"], "safety_unavailable")
    monkeypatch.undo()


async def check_encryption_and_ownership(
    client: httpx.AsyncClient, database: Any, report: Report, monkeypatch: Any
) -> None:
    """The note in the raw column, and what a second account can see."""
    print("\n[4] Encryption at rest, and ownership")

    _owner_id, owner_headers = await make_person(client)
    stranger_id, stranger_headers = await make_person(client)
    monkeypatch.setattr("app.api.v1.mood._now", lambda: datetime(2026, 3, 15, 12, 0, tzinfo=UTC))

    secret = "I told nobody about the hospital appointment on Tuesday."
    created = await client.post(
        MOOD,
        headers=owner_headers,
        json={
            "valence": 2,
            "energy": 2,
            "emotions": ["anxiety"],
            "factors": ["health"],
            "note": secret,
            "tz": "Asia/Kolkata",
        },
    )
    entry_id = uuid.UUID(created.json()["entry"]["id"])

    # The raw column, read as bytes: the plaintext must not be in it, and it must
    # not have leaked into any other column of any row either.
    async with database.session_factory() as session:
        blob = (
            await session.execute(select(MoodEntry.note_encrypted).where(MoodEntry.id == entry_id))
        ).scalar_one()
        table_dump = (await session.execute(sa_text("SELECT * FROM mood_entries"))).all()
        events_dump = (await session.execute(sa_text("SELECT * FROM safety_events"))).all()
        safety_columns = (await session.execute(sa_text("PRAGMA table_info(safety_events)"))).all()
    column_names = [row[1] for row in safety_columns]
    report.add(
        "the note is stored as ciphertext, not text",
        blob is not None and secret.encode() not in blob,
        f"{len(blob or b'')} bytes, plaintext not present",
    )
    report.add(
        "the ciphertext is longer than the plaintext (Fernet envelope)",
        blob is not None and len(blob) > len(secret.encode()),
        f"{len(blob or b'')} > {len(secret.encode())}",
    )
    report.add(
        "no column of any mood row contains the note",
        all(secret not in str(value) for row in table_dump for value in row),
        f"{len(table_dump)} row(s), {len(table_dump[0]) if table_dump else 0} column(s) scanned",
    )
    report.add(
        "there is no text column on safety_events to leak into",
        not any(
            name in {"text", "message", "note", "content", "excerpt", "detail"}
            for name in column_names
        ),
        f"columns: {column_names}",
    )
    report.add(
        "no safety event contains the note",
        all(secret not in str(value) for row in events_dump for value in row),
        f"{len(events_dump)} event row(s) scanned",
    )
    report.add(
        "the owner still reads their own note back",
        created.json()["entry"]["note"] == secret,
        "note returned to its owner",
    )

    # Ownership.
    stranger_list = (await client.get(f"{MOOD}?tz=Asia/Kolkata", headers=stranger_headers)).json()
    report.expect("a stranger's list is empty", stranger_list["count"], 0)
    stranger_delete = await client.delete(f"{MOOD}/{entry_id}", headers=stranger_headers)
    report.expect("deleting somebody else's check-in is a 404", stranger_delete.status_code, 404)
    stranger_streak = (await client.get(STREAK, headers=stranger_headers)).json()
    report.expect("a stranger's streak is untouched", stranger_streak["total_check_ins"], 0)
    unauthenticated = await client.get(MOOD)
    report.expect("no token is a 401", unauthenticated.status_code, 401)
    assert stranger_id  # (the account exists; it simply owns nothing)

    # Deleting your own.
    deleted = await client.delete(f"{MOOD}/{entry_id}?tz=Asia/Kolkata", headers=owner_headers)
    report.expect("the owner can delete their own check-in", deleted.status_code, 200)
    after = (await client.get(f"{MOOD}?tz=Asia/Kolkata", headers=owner_headers)).json()
    report.expect("and it is gone", after["count"], 0)
    report.expect("the deleted id is reported", deleted.json()["id"], str(entry_id))


def check_copy(report: Report) -> None:
    """Print every mood string and scan it with an independent blocklist."""
    print("\n[5] Every mood string the UI can show, scanned for guilt and cheerleading")

    bundle = Path("/tmp/manovia-mood-copy-dump.mjs")
    build = subprocess.run(
        [
            "npx",
            "esbuild",
            "scripts/mood-copy-dump.ts",
            "--bundle",
            "--format=esm",
            "--platform=node",
            "--alias:@=./src",
            f"--outfile={bundle}",
        ],
        cwd=FRONTEND_ROOT,
        capture_output=True,
        text=True,
    )
    if build.returncode != 0:
        report.add("the frontend copy could be dumped", False, build.stderr.strip()[-200:])
        return
    run = subprocess.run(["node", str(bundle)], capture_output=True, text=True)
    if run.returncode != 0:
        report.add("the frontend copy could be dumped", False, run.stderr.strip()[-200:])
        return

    payload = json.loads(run.stdout)
    sentences: list[str] = payload["strings"]
    vocabulary: list[str] = [value for value in payload["extra"] if value not in sentences]

    def scan(values: list[str], phrases: tuple[str, ...], kind: str) -> list[tuple[str, str]]:
        hits: list[tuple[str, str]] = []
        for value in values:
            lowered = value.casefold()
            for phrase in phrases:
                if phrase in lowered:
                    hits.append((value, f"{kind}: {phrase!r}"))
        return hits

    flagged = scan(sentences, GUILT_PHRASES, "guilt") + scan(
        sentences, CHEERLEADING_PHRASES, "cheerleading"
    )
    vocabulary_hits = scan(vocabulary, VOCABULARY_JUDGEMENT_PHRASES, "judgement")

    print(f"    {len(sentences)} sentences:")
    for value in sentences:
        print(f"      · {value}")
    print(f"    {len(vocabulary)} vocabulary words (emotions, factors, scale labels):")
    print("      · " + " · ".join(vocabulary))

    report.add(
        "no judgmental word in the check-in vocabulary",
        not vocabulary_hits,
        "; ".join(f"{value!r} ({reason})" for value, reason in vocabulary_hits) or "clean",
    )

    report.add(
        "no guilt-inducing wording in any mood string",
        not any(reason.startswith("guilt") for _, reason in flagged),
        "; ".join(
            f"{value!r} ({reason})" for value, reason in flagged if reason.startswith("guilt")
        )
        or "clean",
    )
    report.add(
        "no toxic-positivity wording in any mood string",
        not any(reason.startswith("cheerleading") for _, reason in flagged),
        "; ".join(
            f"{value!r} ({reason})"
            for value, reason in flagged
            if reason.startswith("cheerleading")
        )
        or "clean",
    )

    # The frontend vocabulary must be the backend vocabulary, or a check-in the
    # UI offers will be rejected by the server it was built for.
    from app.services.mood import (
        EMOTION_TAGS,
        MAX_EMOTIONS,
        MAX_FACTORS,
        MOOD_FACTORS,
        NOTE_MAX_CHARS,
    )

    taxonomy = payload["taxonomy"]
    report.expect(
        "frontend emotion ids == backend taxonomy", taxonomy["emotions"], list(EMOTION_TAGS)
    )
    report.expect("frontend factor ids == backend factors", taxonomy["factors"], list(MOOD_FACTORS))
    report.expect("emotion cap matches", taxonomy["max_emotions"], MAX_EMOTIONS)
    report.expect("factor cap matches", taxonomy["max_factors"], MAX_FACTORS)
    report.expect("note cap matches", taxonomy["note_max_chars"], NOTE_MAX_CHARS)
    report.note(f"{len(sentences)} mood sentences and {len(vocabulary)} vocabulary words reviewed")


def check_frontend_suite(report: Report) -> None:
    """Run the frontend unit suite as one of the checks (opt-in: it is slow)."""
    print("\n[6] Frontend unit suite (vitest)")
    result = subprocess.run(
        ["npx", "vitest", "run", "--reporter", "dot"],
        cwd=FRONTEND_ROOT,
        capture_output=True,
        text=True,
    )
    # Pick the summary lines rather than the last lines of output: a passing run
    # still ends with React warnings, which would make the table lie about itself.
    # Colour codes are stripped because this text ends up in a table.
    ansi = re.compile(r"\x1b\[[0-9;]*m")
    combined = ansi.sub("", result.stdout + result.stderr).splitlines()
    summary = [
        line.strip()
        for line in combined
        if "Test Files" in line or line.strip().startswith("Tests ")
    ]
    report.add(
        "frontend vitest suite",
        result.returncode == 0,
        " | ".join(summary[-2:]) if summary else "no summary line found",
    )


# --------------------------------------------------------------------------- #
# Entry point                                                                    #
# --------------------------------------------------------------------------- #


class _Monkeypatch:
    """A tiny stand-in for pytest's monkeypatch, for a script that is not a test."""

    def __init__(self) -> None:
        self._undo: list[Any] = []

    def setattr(self, target: str, value: Any, raising: bool = True) -> None:
        module_name, _, attribute = target.rpartition(".")
        module = __import__(module_name, fromlist=[attribute])
        self._undo.append((module, attribute, getattr(module, attribute)))
        setattr(module, attribute, value)

    def undo(self) -> None:
        while self._undo:
            module, attribute, original = self._undo.pop()
            setattr(module, attribute, original)


def print_table(checks: list[Check]) -> None:
    width = max(len(check.name) for check in checks) + 2
    print("\n" + "=" * (width + 46))
    print(f"{'CHECK':<{width}}RESULT  DETAIL")
    print("-" * (width + 46))
    for check in checks:
        name, status, detail = check.as_row()
        print(f"{name:<{width}}{status:<8}{detail[:70]}")
    passed = sum(1 for check in checks if check.passed)
    print("-" * (width + 46))
    print(f"{passed}/{len(checks)} checks passed")
    print("=" * (width + 46))


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--frontend",
        action="store_true",
        help="also run the frontend vitest suite (slower)",
    )
    args = parser.parse_args()

    report = Report()
    app, database = build_app(Path("/tmp/manovia_verify_day15.db"))
    await create_tables(database)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        monkeypatch = _Monkeypatch()
        await check_range_queries(client, database, report)
        await check_streak(client, database, report, monkeypatch)
        await check_high_risk_note(client, database, report, monkeypatch)
        await check_encryption_and_ownership(client, database, report, monkeypatch)

    check_copy(report)
    if args.frontend:
        check_frontend_suite(report)

    print_table(report.checks)
    if report.notes:
        print("\nNotes:")
        for note in report.notes:
            print(f"  · {note}")

    failed = [check for check in report.checks if not check.passed]
    print(f"\n{len(report.checks) - len(failed)}/{len(report.checks)} checks passed.")
    if failed:
        print("FAILED CHECKS:")
        for check in failed:
            print(f"  - {check.name}: {check.detail}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
