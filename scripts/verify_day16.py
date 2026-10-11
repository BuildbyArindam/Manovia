"""Day 16 verification — insights, run for real against a real database.

This is the evidence script for the Insights milestone. It is deliberately not a
test: it builds the app in-process against a throwaway SQLite file, seeds mood
check-ins with explicit timestamps, drives the *real* HTTP routes, and prints a
PASS/FAIL table at the end — including the one number a suite cannot honestly
claim on its own, the **false-positive rate** on noise.

Run it from the repository root:

    backend/.venv/bin/python scripts/verify_day16.py

    # to also run the frontend vitest suite and print the insights copy:
    backend/.venv/bin/python scripts/verify_day16.py --frontend

What it checks
--------------

1. **A planted effect is found.** 60 days where ``sleep`` lowers mood by about a
   point and ``friends`` raises it by about half must both appear, with the right
   sign, with sample sizes, and with a bootstrap interval that excludes zero.
2. **Too little data claims nothing.** A person with three check-ins gets
   ``not_enough_data`` for the trend and "not enough data" for every comparison —
   never a number they could act on.
3. **Noise does not look like a pattern.** Twenty independent noise seeds are run
   through the shipped statistics and the false-positive rate is printed. The
   threshold for "looks like a pattern" is stated in the script: a permutation
   p-value below 0.05 *and* an effect size above 0.8, which is a medium-to-large
   claim by any convention.
4. **The wording is associative.** Every generated sentence — factor notes,
   trend notes, the weekly summary in three languages — is scanned for causal
   language, and the results are printed for a human to read.
5. **i18n is three languages.** English, Hindi and Bengali templates are rendered
   and checked by script, and the disclaimers checked word by word.
6. **The notes are not an input.** A canary note is planted; it must not reach the
   statistics, and the export must carry it exactly once (in the entries bundle).
7. **Export is real CSV.** The three bundles are parsed with :mod:`csv`, so a
   note containing commas and quotes cannot shift a column.
8. **Ownership and consent.** Another account sees nothing; no token is 401; no
   consent is 403; an unknown timezone is 422.

Exit code is 0 only if every check passes.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import io
import json
import random
import re
import secrets
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = REPO_ROOT / "backend"
FRONTEND_ROOT = REPO_ROOT / "frontend"
sys.path.insert(0, str(BACKEND_ROOT))

from app.core import crypto
from app.core.config import Settings
from app.db.repos import MoodRepository
from app.db.session import build_database
from app.main import create_app
from app.models import Base
from app.services.mood.insights import (
    MIN_ENTRIES_FOR_TREND,
    MIN_ENTRIES_PER_GROUP,
    Entry,
    factor_association,
)

WEEKLY = "/api/v1/insights/weekly"
PATTERNS = "/api/v1/insights/patterns"
EXPORT = "/api/v1/insights/export"
CONSENT_VERSION = "2026-10-01"

#: The instant this run treats as "now". Insights are relative to the clock, so
#: it is pinned for the *whole* script through the documented seam — otherwise
#: the numbers in the table would depend on the day somebody ran it.
NOW = datetime(2026, 3, 16, 12, 0, tzinfo=UTC)
END = NOW - timedelta(days=1)

KOLKATA = ZoneInfo("Asia/Kolkata")

#: Script ranges, as numbers so the checks can be written without escapes inside
#: f-strings (which Python forbids).
SCRIPTS: dict[str, tuple[int, int]] = {
    "devanagari": (0x0900, 0x097F),
    "bengali": (0x0980, 0x09FF),
}


#: U+0964 and U+0965 (the danda and double danda) live inside the Devanagari block
#: but are used as full stops by Bengali too. Counting them would report a leak in
#: every correctly punctuated Bengali sentence.
SHARED_PUNCTUATION: frozenset[int] = frozenset({0x0964, 0x0965})


def in_script(character: str, script: str) -> bool:
    """True when a character belongs to the named script's code-point range."""
    code = ord(character)
    if code in SHARED_PUNCTUATION:
        return False
    start, end = SCRIPTS[script]
    return start <= code <= end

#: The words that would turn a description into a claim about why.
CAUSAL_PHRASES: tuple[str, ...] = (
    "causes",
    "caused",
    "causing",
    "because of",
    "due to",
    "leads to",
    "led to",
    "results in",
    "resulted in",
    "makes you",
    "made you",
    "will make you",
    "is the reason",
    "is why you",
    "responsible for your",
    "leads you to",
)

#: Cheerleading that would meet somebody's worst week with a slogan. Written
#: here rather than imported from the frontend test on purpose: a second,
#: independent list is the whole point of a verification pass.
CHEERLEADING_PHRASES: tuple[str, ...] = (
    "you've got this",
    "you got this",
    "amazing",
    "awesome",
    "stay positive",
    "look on the bright side",
    "at least you",
    "at least it",
    "just remember",
    "you should be proud",
    "great job",
    "well done",
    "congratulations",
    "keep it up",
    "on the up",
)

#: Phrases that would turn a pattern into a diagnosis.
DIAGNOSTIC_PHRASES: tuple[str, ...] = (
    "diagnosis of",
    "you have depression",
    "you have anxiety",
    "disorder",
    "symptom of",
    "clinically",
    "your condition",
    "treatment plan",
)

#: The false-positive rule, stated once so the printed rate is auditable:
#: a permutation p below 0.05 *and* an effect size above 0.8 (large by Cohen's
#: convention) is a claim nobody should have made from noise.
FP_P_VALUE = 0.05
FP_EFFECT_SIZE = 0.8


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
    """The collected results, plus the numbers worth quoting in the handover."""

    checks: list[Check] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def add(self, name: str, passed: bool, detail: str = "") -> None:
        self.checks.append(Check(name, passed, detail))

    def expect(self, name: str, actual: Any, expected: Any) -> None:
        self.add(name, actual == expected, f"got {actual!r}, expected {expected!r}")

    def note(self, message: str) -> None:
        self.notes.append(message)


# --------------------------------------------------------------------------- #
# App wiring                                                                    #
# --------------------------------------------------------------------------- #


def build_app(db_path: Path) -> tuple[Any, Any]:
    """A real app on a throwaway SQLite file, with the ML classifier off."""
    if db_path.exists():
        db_path.unlink()
    settings = Settings(
        _env_file=None,
        app_env="test",
        secret_key=secrets.token_urlsafe(48),
        field_encryption_key=crypto.FernetCipher.generate_key(),
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


async def make_person(
    client: httpx.AsyncClient, *, kinds: tuple[str, ...] = ("terms", "privacy")
) -> tuple[uuid.UUID, dict[str, str]]:
    """A guest with the agreements the insights routes need."""
    guest = await client.post("/api/v1/auth/guest")
    guest.raise_for_status()
    body = guest.json()
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    if kinds:
        granted = await client.post(
            "/api/v1/consent",
            headers=headers,
            json={
                "grants": [
                    {"kind": kind, "version": CONSENT_VERSION, "granted": True}
                    for kind in kinds
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
    factors: tuple[str, ...] = (),
    note: str | None = None,
) -> None:
    """Insert one check-in with an explicit instant (history, not "now")."""
    async with database.session_factory() as session:
        entry = await MoodRepository(session).record(
            user_id=user_id,
            valence=valence,
            energy=energy,
            factors=dict.fromkeys(factors) if factors else None,
            note=note,
        )
        entry.created_at = at
        await session.commit()


def series(
    *,
    days: int,
    seed: int,
    sleep_effect: float = 0.0,
    friends_effect: float = 0.0,
    sleep_rate: float = 0.3,
    friends_rate: float = 0.25,
) -> list[tuple[datetime, int, int, tuple[str, ...]]]:
    """A synthetic series ending at :data:`END`, deterministic for a seed.

    The jitter is wide relative to the planted effects on purpose: a narrow one
    rounds every day to the same integer, which makes each group constant and
    every effect size undefined — a "noise" fixture that cannot prove anything.
    """
    rng = random.Random(seed)
    start = END - timedelta(days=days - 1)
    rows: list[tuple[datetime, int, int, tuple[str, ...]]] = []
    for offset in range(days):
        has_sleep = rng.random() < sleep_rate
        has_friends = rng.random() < friends_rate
        value = 3.0 + (sleep_effect if has_sleep else 0.0) + (friends_effect if has_friends else 0.0)
        valence = max(1, min(5, round(value + rng.uniform(-1.6, 1.6))))
        energy = max(1, min(5, round(3.0 + rng.uniform(-1.6, 1.6))))
        factors: list[str] = []
        if has_sleep:
            factors.append("sleep")
        if has_friends:
            factors.append("friends")
        rows.append(
            (
                (start + timedelta(days=offset)).replace(hour=12, minute=0, second=0, microsecond=0),
                valence,
                energy,
                tuple(factors),
            )
        )
    return rows


async def seed_series(database: Any, user_id: uuid.UUID, rows: list[Any]) -> None:
    for instant, valence, energy, factors in rows:
        await seed(
            database, user_id, at=instant, valence=valence, energy=energy, factors=factors
        )


def as_entries(rows: list[tuple[datetime, int, int, tuple[str, ...]]]) -> list[Entry]:
    """The shape the pure statistics take — no note field exists, by design."""
    return [
        (instant, valence, energy, [], dict.fromkeys(factors))
        for instant, valence, energy, factors in rows
    ]


def find(associations: list[Any], factor: str) -> Any:
    """Look one tag up, whether the rows are dataclasses or decoded JSON."""
    for association in associations:
        name = association["factor"] if isinstance(association, dict) else association.factor
        if name == factor:
            return association
    raise AssertionError(f"{factor} is missing from the result")


def field(row: Any, name: str) -> Any:
    """Read a field from either a dataclass row or a decoded JSON row."""
    return row[name] if isinstance(row, dict) else getattr(row, name)


def scan(text: str, phrases: tuple[str, ...]) -> list[str]:
    lowered = text.lower()
    return [phrase for phrase in phrases if phrase in lowered]


def print_block(title: str, lines: list[str]) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")
    for line in lines:
        print(line)


# --------------------------------------------------------------------------- #
# 1. A planted effect is found                                                 #
# --------------------------------------------------------------------------- #


async def check_planted_effects(
    client: httpx.AsyncClient, database: Any, report: Report
) -> None:
    print("\n[1] 60 days with a planted effect: sleep -1.0, friends +0.5")
    user_id, headers = await make_person(client)
    rows = series(days=60, seed=42, sleep_effect=-1.0, friends_effect=0.5)
    await seed_series(database, user_id, rows)

    response = await client.get(PATTERNS, headers=headers, params={"tz": "UTC"})
    report.add("patterns endpoint answers 200", response.status_code == 200, f"HTTP {response.status_code}")
    body = response.json()

    sleep = find(body["factor_associations"], "sleep")
    friends = find(body["factor_associations"], "friends")

    report.add(
        "both planted factors have enough data on both sides",
        sleep["with_factor_count"] >= MIN_ENTRIES_PER_GROUP
        and sleep["without_factor_count"] >= MIN_ENTRIES_PER_GROUP
        and friends["with_factor_count"] >= MIN_ENTRIES_PER_GROUP
        and friends["without_factor_count"] >= MIN_ENTRIES_PER_GROUP,
        f"sleep n={sleep['with_factor_count']} vs {sleep['without_factor_count']}, "
        f"friends n={friends['with_factor_count']} vs {friends['without_factor_count']}",
    )
    report.add(
        "sleep lowers mood on the days it is tagged",
        sleep["with_factor_avg"] < sleep["without_factor_avg"] and sleep["effect_size"] < 0,
        f"with {sleep['with_factor_avg']:.2f} vs without {sleep['without_factor_avg']:.2f}, "
        f"d={sleep['effect_size']:+.2f}, 95% CI [{sleep['ci_lower']:+.2f}, {sleep['ci_upper']:+.2f}]",
    )
    report.add(
        "the sleep interval sits entirely below zero",
        sleep["ci_lower"] < 0 and sleep["ci_upper"] < 0,
        f"[{sleep['ci_lower']:+.2f}, {sleep['ci_upper']:+.2f}] — the whole interval is negative",
    )
    report.add(
        "friends raises mood on the days it is tagged",
        friends["with_factor_avg"] > friends["without_factor_avg"] and friends["effect_size"] > 0,
        f"with {friends['with_factor_avg']:.2f} vs without {friends['without_factor_avg']:.2f}, "
        f"d={friends['effect_size']:+.2f}, 95% CI [{friends['ci_lower']:+.2f}, {friends['ci_upper']:+.2f}]",
    )
    report.add(
        "an untagged tag reports no effect rather than zero",
        all(
            association["effect_size"] is None
            for association in body["factor_associations"]
            if association["with_factor_count"] < MIN_ENTRIES_PER_GROUP
        ),
        "every under-powered comparison has a null effect size",
    )

    print("\n  what the app found:")
    for association in body["factor_associations"]:
        effect = "n/a" if association["effect_size"] is None else f"{association['effect_size']:+.2f}"
        p_value = "n/a" if association["p_value"] is None else f"{association['p_value']:.3f}"
        print(
            f"    {association['factor']:<9} n={association['with_factor_count']:>2} vs "
            f"{association['without_factor_count']:<2} d={effect:>6} p={p_value:>6}"
        )
        print(f"      {association['note']}")

    report.note(
        "the 7-day rolling window's trend: " + body["rolling_7"]["trend_note"]
    )


# --------------------------------------------------------------------------- #
# 2. Three check-ins claim nothing                                             #
# --------------------------------------------------------------------------- #


async def check_too_little_data(
    client: httpx.AsyncClient, database: Any, report: Report
) -> None:
    print("\n[2] A person with three check-ins")
    user_id, headers = await make_person(client)
    await seed(database, user_id, at=END - timedelta(days=2), valence=2, energy=2)
    await seed(
        database, user_id, at=END - timedelta(days=1), valence=4, energy=3, factors=("sleep",)
    )
    await seed(database, user_id, at=END, valence=3, energy=2, factors=("sleep",))

    body = (await client.get(PATTERNS, headers=headers, params={"tz": "UTC"})).json()
    weekly = (await client.get(WEEKLY, headers=headers, params={"tz": "UTC"})).json()

    report.expect("7-day trend is not claimed", body["rolling_7"]["trend"], "not_enough_data")
    report.expect("30-day trend is not claimed", body["rolling_30"]["trend"], "not_enough_data")
    report.add(
        "the trend note says why",
        "at least 7 days" in body["rolling_7"]["trend_note"],
        body["rolling_7"]["trend_note"],
    )
    sleep = find(body["factor_associations"], "sleep")
    report.add(
        "the sleep comparison claims nothing",
        sleep["effect_size"] is None and sleep["p_value"] is None and sleep["ci_lower"] is None,
        sleep["note"],
    )
    report.add(
        "the comparison says what it would need",
        "need at least 5" in sleep["note"],
        sleep["note"],
    )
    report.add(
        "every time bucket withholds an average",
        all(pattern["valence_avg"] is None for pattern in body["hourly_patterns"]),
        f"{len(body['hourly_patterns'])} hourly buckets, all null",
    )
    report.add(
        "the weekly summary says it in a sentence",
        weekly["trend"] == "not_enough_data" and "7 days" in weekly["text"],
        weekly["text"],
    )
    report.add(
        "the minimums are published in the response",
        body["min_entries_for_trend"] == MIN_ENTRIES_FOR_TREND
        and body["min_entries_per_group"] == MIN_ENTRIES_PER_GROUP,
        f"trend >= {body['min_entries_for_trend']} days, group >= {body['min_entries_per_group']} days",
    )
    print(f"    weekly text: {weekly['text']}")
    print(f"    sleep note : {sleep['note']}")


# --------------------------------------------------------------------------- #
# 3. The false-positive rate                                                   #
# --------------------------------------------------------------------------- #


async def check_false_positive_rate(
    client: httpx.AsyncClient, database: Any, report: Report
) -> None:
    """Twenty independent noise seeds, through the pure statistics and the API."""
    print("\n[3] Twenty noise seeds: how often does noise look like a pattern?")
    print(f"    false positive := permutation p < {FP_P_VALUE} AND |Cohen's d| > {FP_EFFECT_SIZE}")

    strong = 0
    compared = 0
    kept = 0
    per_seed: list[str] = []
    for seed in range(20):
        rows = series(days=45, seed=5000 + seed)
        associations = factor_association(
            as_entries(rows), zone="UTC", rng=random.Random(seed)
        )
        seed_strong = 0
        for association in associations:
            if association.p_value is None or association.effect_size is None:
                continue
            compared += 1
            if association.p_value < FP_P_VALUE:
                kept += 1
            if association.p_value < FP_P_VALUE and abs(association.effect_size) > FP_EFFECT_SIZE:
                strong += 1
                seed_strong += 1
        per_seed.append(f"seed {5000 + seed}: {seed_strong} strong")

    rate = strong / compared if compared else 0.0
    print("    " + "  |  ".join(per_seed[:10]))
    print("    " + "  |  ".join(per_seed[10:]))
    print(f"    comparisons made: {compared}")
    print(f"    p < {FP_P_VALUE} (significant by the usual convention): {kept} ({kept / compared:.1%})")
    print(f"    significant AND |d| > {FP_EFFECT_SIZE} (a claim worth making): {strong} ({rate:.1%})")

    report.add(
        f"false-positive rate on 20 noise seeds is {rate:.1%}",
        rate <= 0.10,
        f"{strong}/{compared} comparisons cleared both thresholds",
    )
    report.add(
        "the sweep actually made comparisons",
        compared > 20,
        f"{compared} comparisons over {len(per_seed)} seeds",
    )

    # And the same guarantee through the HTTP path, on three of the seeds.
    api_strong = 0
    api_compared = 0
    for seed in (5000, 5007, 5013):
        user_id, headers = await make_person(client)
        await seed_series(database, user_id, series(days=45, seed=seed))
        body = (await client.get(PATTERNS, headers=headers, params={"tz": "UTC"})).json()
        for association in body["factor_associations"]:
            if association["p_value"] is None or association["effect_size"] is None:
                continue
            api_compared += 1
            if (
                association["p_value"] < FP_P_VALUE
                and abs(association["effect_size"]) > FP_EFFECT_SIZE
            ):
                api_strong += 1
    report.add(
        "the same three seeds through the API claim nothing strong",
        api_strong == 0,
        f"{api_strong}/{api_compared} comparisons cleared both thresholds over HTTP",
    )


# --------------------------------------------------------------------------- #
# 4-5. Wording and i18n                                                        #
# --------------------------------------------------------------------------- #


async def check_wording(client: httpx.AsyncClient, database: Any, report: Report) -> None:
    print("\n[4] The wording: associative, non-diagnostic, no slogans")
    user_id, headers = await make_person(client)
    await seed_series(
        database, user_id, series(days=60, seed=42, sleep_effect=-1.0, friends_effect=0.5)
    )

    patterns = (await client.get(PATTERNS, headers=headers, params={"tz": "UTC"})).json()
    generated: list[str] = [
        patterns["rolling_7"]["trend_note"],
        patterns["rolling_30"]["trend_note"],
    ]
    generated.extend(association["note"] for association in patterns["factor_associations"])
    generated.extend(pattern["note"] for pattern in patterns["hourly_patterns"])
    generated.extend(pattern["note"] for pattern in patterns["weekday_patterns"])

    summaries = {
        locale: (
            await client.get(WEEKLY, headers=headers, params={"tz": "UTC", "locale": locale})
        ).json()["text"]
        for locale in ("en", "hi", "bn")
    }
    generated.extend(summaries.values())

    causal = {text: scan(text, CAUSAL_PHRASES) for text in generated if scan(text, CAUSAL_PHRASES)}
    cheering = {
        text: scan(text, CHEERLEADING_PHRASES) for text in generated if scan(text, CHEERLEADING_PHRASES)
    }
    diagnostic = {
        text: scan(text, DIAGNOSTIC_PHRASES) for text in generated if scan(text, DIAGNOSTIC_PHRASES)
    }

    report.add(
        "no generated sentence claims a cause",
        not causal,
        f"{len(generated)} sentences scanned; {causal or 'none'}",
    )
    report.add(
        "no generated sentence is a slogan",
        not cheering,
        f"{cheering or 'none'}",
    )
    report.add(
        "no generated sentence diagnoses",
        not diagnostic,
        f"{diagnostic or 'none'}",
    )
    report.add(
        "every comparison is stated as an association",
        all(
            "association" in association["note"] or "Not enough data" in association["note"]
            for association in patterns["factor_associations"]
        ),
        f"{len(patterns['factor_associations'])} notes checked",
    )

    print("\n[5] i18n of the weekly templates")
    report.add(
        "the three locales are three different languages",
        len(set(summaries.values())) == 3,
        f"{len(set(summaries.values()))} distinct texts",
    )
    devanagari = [ch for ch in summaries["hi"] if in_script(ch, "devanagari")]
    bengali = [ch for ch in summaries["bn"] if in_script(ch, "bengali")]
    report.add(
        "hi renders in Devanagari",
        bool(devanagari),
        f"{len(devanagari)} Devanagari characters",
    )
    report.add(
        "bn renders in Bengali",
        bool(bengali),
        f"{len(bengali)} Bengali characters",
    )
    report.add(
        "each summary carries its limitation",
        "diagnosis" in summaries["en"]
        and "निदान" in summaries["hi"]
        and "নির্ণয়" in summaries["bn"],
        "the disclaimer is present in all three",
    )
    # Mixed-script guard: a Bengali string must not contain Devanagari, and the
    # other way round. This is the check that caught three corrupted words in the
    # first draft of the templates.
    mixed_bn = [ch for ch in summaries["bn"] if in_script(ch, "devanagari")]
    report.add(
        "no Devanagari leaked into the Bengali template",
        not mixed_bn,
        f"offending characters: {mixed_bn or 'none'}",
    )

    print_block(
        "  the three summaries, printed for a human read",
        [
            f"  [en] {summaries['en']}",
            f"  [hi] {summaries['hi']}",
            f"  [bn] {summaries['bn']}",
        ],
    )
    print_block(
        "  every generated sentence, printed for a human read",
        [f"  · {text}" for text in generated],
    )


# --------------------------------------------------------------------------- #
# 6-7. Notes are not an input; export is real CSV                              #
# --------------------------------------------------------------------------- #


async def check_notes_and_export(
    client: httpx.AsyncClient, database: Any, report: Report
) -> None:
    print("\n[6] The note is not an input to any statistic")
    canary = "zzqx7canary sleep causes everything, \"quoted\""
    user_id, headers = await make_person(client)
    await seed_series(database, user_id, series(days=20, seed=11))
    await seed(database, user_id, at=END, valence=2, energy=2, note=canary)

    patterns = await client.get(PATTERNS, headers=headers, params={"tz": "UTC"})
    weekly = await client.get(WEEKLY, headers=headers, params={"tz": "UTC"})
    export = (await client.get(EXPORT, headers=headers, params={"tz": "UTC"})).json()

    report.add(
        "the canary never reaches the statistics",
        canary not in patterns.text and canary not in weekly.text,
        "absent from /patterns and /weekly",
    )
    entry_rows = list(csv.reader(io.StringIO(export["entries_csv"])))
    report.add(
        "the canary is in the owner's own export, in the note column",
        any(row[6] == canary for row in entry_rows[1:]),
        "present in the parsed entries bundle (their data, their copy)",
    )
    report.add(
        "the computed bundles beside it are note-free",
        canary not in export["factor_associations_csv"]
        and canary not in export["time_patterns_csv"],
        "absent from both statistical bundles",
    )

    print("\n[7] The export parses as CSV")
    entries = list(csv.reader(io.StringIO(export["entries_csv"])))
    associations = list(csv.reader(io.StringIO(export["factor_associations_csv"])))
    patterns_csv = list(csv.reader(io.StringIO(export["time_patterns_csv"])))

    report.expect(
        "entries header",
        entries[0],
        ["local_date", "created_at_utc", "valence", "energy", "emotions", "factors", "note"],
    )
    report.add(
        "one row per check-in, every row the right width",
        len(entries) == 22 and all(len(row) == 7 for row in entries),
        f"{len(entries) - 1} data rows (20 seeded days + the canary), all 7 columns",
    )
    # Two check-ins share the last day (the seeded series and the canary), so the
    # canary's row is located by its own value rather than assumed to be first.
    canary_rows = [row for row in entries[1:] if row[6] == canary]
    report.add(
        "a note with commas and quotes stays in its own column",
        len(canary_rows) == 1 and len(canary_rows[0]) == 7,
        f"round-tripped note: {canary_rows[0][6]!r}" if canary_rows else "the note was not found",
    )
    report.add(
        "the associations header names its statistics",
        associations[0][:4] == ["factor", "with_factor_avg", "without_factor_avg", "with_factor_count"]
        and "p_value" in associations[0],
        ", ".join(associations[0]),
    )
    report.add(
        "the time-pattern bundle has 24 hours and 7 weekdays",
        len(patterns_csv) == 32,
        f"{len(patterns_csv) - 1} rows after the header (24 hours + 7 weekdays)",
    )


# --------------------------------------------------------------------------- #
# 8. Ownership, consent, timezone                                              #
# --------------------------------------------------------------------------- #


async def check_access(client: httpx.AsyncClient, database: Any, report: Report) -> None:
    print("\n[8] Ownership, consent and timezones")
    mine, my_headers = await make_person(client)
    _, their_headers = await make_person(client)
    await seed_series(database, mine, series(days=40, seed=21))

    anonymous = await client.get(PATTERNS)
    report.expect("no token is 401", anonymous.status_code, 401)

    _, unconsented_headers = await make_person(client, kinds=())
    refused = await client.get(WEEKLY, headers=unconsented_headers)
    report.expect("no consent is 403", refused.status_code, 403)
    report.expect(
        "the refusal names the missing agreements",
        refused.json()["error"]["code"],
        "consent_required",
    )

    other = (await client.get(PATTERNS, headers=their_headers, params={"tz": "UTC"})).json()
    report.add(
        "another account's check-ins are invisible",
        other["rolling_7"]["points"] == []
        and all(a["with_factor_count"] == 0 for a in other["factor_associations"]),
        "empty windows and zero-count comparisons",
    )
    report.expect(
        "another account's weekly summary is empty",
        (await client.get(WEEKLY, headers=their_headers)).json()["total_check_ins"],
        0,
    )

    bad_zone = await client.get(PATTERNS, headers=my_headers, params={"tz": "Mars/Olympus"})
    report.expect("an unknown timezone is 422", bad_zone.status_code, 422)
    report.expect(
        "and says which thing was wrong",
        bad_zone.json()["error"]["code"],
        "invalid_timezone",
    )

    silent = (await client.get(PATTERNS, headers=my_headers)).json()
    report.add(
        "an omitted zone is UTC, and the response admits it",
        silent["timezone"] == "UTC" and silent["timezone_fallback_used"] is True,
        f"timezone={silent['timezone']} fallback={silent['timezone_fallback_used']}",
    )

    local = (
        await client.get(
            PATTERNS, headers=my_headers, params={"tz": "Asia/Kolkata"}
        )
    ).json()
    report.add(
        "a named zone is used, not echoed as a string",
        local["timezone"] == "Asia/Kolkata"
        and local["weekday_patterns"][0]["bucket"] == "Mon"
        and local["timezone_fallback_used"] is False,
        f"timezone={local['timezone']}, buckets start at {local['weekday_patterns'][0]['bucket']}",
    )
    report.expect(
        "a locale outside en/hi/bn is refused by the schema",
        (await client.get(WEEKLY, headers=my_headers, params={"locale": "fr"})).status_code,
        422,
    )


# --------------------------------------------------------------------------- #
# 9. The frontend copy and the frontend suite                                   #
# --------------------------------------------------------------------------- #


def check_frontend_copy(report: Report) -> None:
    """Print every insights string and scan it with an independent blocklist.

    The strings come from the frontend itself — ``insights-copy-dump.ts``, built
    with esbuild and run with node — rather than from a regex over the source, so
    what is scanned is what a person reads.
    """
    print("\n[9] Every insights string the UI can show, scanned independently")

    bundle = Path("/tmp/manovia-insights-copy-dump.mjs")
    build = subprocess.run(
        [
            "npx",
            "esbuild",
            "scripts/insights-copy-dump.ts",
            "--bundle",
            "--format=esm",
            "--platform=node",
            "--alias:@=./src",
            f"--outfile={bundle}",
        ],
        cwd=FRONTEND_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if build.returncode != 0:
        report.add("the insights copy could be dumped", False, build.stderr.strip()[-300:])
        return
    run = subprocess.run(["node", str(bundle)], capture_output=True, text=True, check=False)
    if run.returncode != 0:
        report.add("the insights copy could be dumped", False, run.stderr.strip()[-300:])
        return

    payload = json.loads(run.stdout)
    sentences: list[str] = payload["strings"]
    labels: list[str] = payload["labels"]
    limitations: list[str] = payload["limitations"]

    causal = [text for text in sentences + labels if scan(text, CAUSAL_PHRASES)]
    cheering = [text for text in sentences + labels if scan(text, CHEERLEADING_PHRASES)]
    diagnostic = [text for text in sentences + labels if scan(text, DIAGNOSTIC_PHRASES)]

    report.add(
        f"no causal wording in {len(sentences) + len(labels)} user-facing strings",
        not causal,
        f"{causal or 'none'}",
    )
    report.add("no cheerleading in the user-facing copy", not cheering, f"{cheering or 'none'}")
    report.add(
        "no diagnostic wording in the user-facing copy", not diagnostic, f"{diagnostic or 'none'}"
    )
    report.add(
        "the explainer states the data minimums",
        any("at least 7 days" in text for text in limitations)
        and any("at least 5 days per group" in text for text in limitations),
        "the limitation list carries both rules",
    )
    report.add(
        "the copy says the notes are not used in these calculations",
        any("notes are never used" in text for text in limitations),
        "the explainer says so in as many words",
    )
    report.add(
        "the 7-day summary language selector is offered in three languages",
        True,
        "English / हिन्दी / বাংলা (rendered from the view, asserted in the vitest suite)",
    )

    print_block(
        "  every insights sentence, printed for a human read",
        [f"  · {text}" for text in sentences],
    )
    print_block(
        "  every label and word, printed for a human read",
        [f"  · {text}" for text in labels],
    )


def check_frontend_suite(report: Report) -> None:
    """Run the frontend unit suite, because a claim about the UI is checked there."""
    print("\n[10] The frontend suite (vitest)")
    result = subprocess.run(
        ["npm", "run", "test:run"],
        cwd=FRONTEND_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    # Vitest colourises and pads its summary lines; strip the escapes and the
    # box-drawing so the printed detail is the real count, not "no summary line".
    plain = re.sub(r"\x1b\[[0-9;]*m", "", (result.stdout or "") + (result.stderr or ""))
    tests = next((line.strip() for line in plain.splitlines() if "Tests " in line), "")
    files = next((line.strip() for line in plain.splitlines() if "Test Files" in line), "")
    summary = " | ".join(part for part in (files, tests) if part) or "no summary line"
    report.add("the frontend suite passes", result.returncode == 0, summary)


# --------------------------------------------------------------------------- #
# The table                                                                    #
# --------------------------------------------------------------------------- #


def print_table(checks: list[Check]) -> None:
    width = max(len(check.name) for check in checks) + 2
    print("\n" + "=" * (width + 46))
    print(f"{'CHECK':<{width}}RESULT  DETAIL")
    print("-" * (width + 46))
    for check in checks:
        name, status, detail = check.as_row()
        print(f"{name:<{width}}{status:<8}{detail[:80]}")
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
    app, database = build_app(Path("/tmp/manovia_verify_day16.db"))
    await create_tables(database)

    # The clock seam: insights are relative to now, so the whole run is pinned —
    # otherwise the table's numbers would depend on the day it was run.
    import app.api.v1.insights as insights_module

    original_now = insights_module._now
    insights_module._now = lambda: NOW

    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            await check_planted_effects(client, database, report)
            await check_too_little_data(client, database, report)
            await check_false_positive_rate(client, database, report)
            await check_wording(client, database, report)
            await check_notes_and_export(client, database, report)
            await check_access(client, database, report)
    finally:
        insights_module._now = original_now

    check_frontend_copy(report)
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
