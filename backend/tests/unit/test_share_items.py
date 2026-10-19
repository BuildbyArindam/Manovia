"""Unit tests for the share catalogue, the payload model and the consent lines.

Three things are being pinned down here:

* the catalogue is complete, ordered and honest about what each item *excludes*
  — those exclusion sentences are the comprehension part of the feature, so a
  test that lets one disappear is a test that lets the UI lie;
* the payload is strict: a selection cannot smuggle in an unticked item, and the
  numbers it reports cannot claim a direction the data does not support;
* the consent lines are rendered from the payload, so the sentence a person
  confirms and the document that gets encrypted cannot drift apart.

The database-backed parts (``build_payload``) run against the same fixtures the
integration test uses, with the entries written at fixed offsets so the trend
arithmetic is deterministic.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import AssessmentBand, AssessmentInstrument, ShareItem
from app.models.mood import MoodEntry
from app.models.user import User
from app.services.sharing.items import (
    CATALOGUE_ORDER,
    DEFAULT_WINDOW_DAYS,
    DIRECTION_NOTE,
    DISCLAIMER,
    DISCLAIMER_TITLE,
    ITEM_CATALOGUE,
    MAX_ASSESSMENTS,
    MAX_NOTE_CHARS,
    MAX_QUESTION_CHARS,
    MAX_QUESTIONS,
    MAX_THOUGHT_RECORDS,
    MIN_ENTRIES_FOR_DIRECTION,
    NEVER_INCLUDED,
    WINDOW_CHOICES,
    ShareContentError,
    SharePayload,
    ShareRequest,
    build_payload,
    consent_lines,
)
from tests.sharing_helpers import NOTE, QUESTIONS, sample_payload

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)

#: The identity line the consent screen shows for the sample person.
EMAIL_LINE = "Identifies you as: someone@example.test"


# --------------------------------------------------------------------------- #
# the catalogue
# --------------------------------------------------------------------------- #


def test_the_catalogue_covers_every_item_once_in_display_order() -> None:
    """Nothing selectable is undocumented, and the order is the UI's order."""

    keys = [item.key for item in ITEM_CATALOGUE]
    assert keys == list(CATALOGUE_ORDER)
    assert set(keys) == set(ShareItem)
    assert len(keys) == len(set(keys))
    assert list(ShareItem) == list(CATALOGUE_ORDER), "the enum order and the catalogue agree"


def test_every_item_says_what_it_includes_and_what_it_leaves_out() -> None:
    """The exclusion sentences are the comprehension screen; they cannot be empty."""

    for item in ITEM_CATALOGUE:
        assert item.label, item.key
        assert len(item.blurb) > 20, item.key
        assert item.includes, item.key
        assert all(sentence.strip() for sentence in item.includes), item.key

    # The items that carry the most sensitive material say so explicitly.
    by_key = {item.key: item for item in ITEM_CATALOGUE}
    assert by_key[ShareItem.THOUGHT_RECORDS].needs_selection is True
    assert by_key[ShareItem.THOUGHT_RECORDS].excludes == ("records you did not tick",)
    assert any("individual questions" in line for line in by_key[ShareItem.ASSESSMENTS].excludes)
    assert any("diagnosis" in line for line in by_key[ShareItem.ASSESSMENTS].excludes)
    assert any("notes" in line for line in by_key[ShareItem.MOOD_TREND].excludes)
    assert any("password" in line for line in by_key[ShareItem.IDENTITY].excludes)
    assert by_key[ShareItem.MOOD_TREND].needs_selection is False
    # Only the thought records ask which rows to include.
    assert [item.key for item in ITEM_CATALOGUE if item.needs_selection] == [
        ShareItem.THOUGHT_RECORDS
    ]


def test_nothing_is_ticked_by_default() -> None:
    """A share starts empty: the person adds items, they do not remove them."""

    assert all(item.default is False for item in ITEM_CATALOGUE)


def test_the_never_included_list_names_the_things_people_worry_about() -> None:
    """Chat, journal, check-in notes, credentials, and anything unticked."""

    joined = " ".join(NEVER_INCLUDED).lower()
    assert "chat" in joined
    assert "journal" in joined
    assert "notes attached to mood check-ins" in joined
    assert "password" in joined
    assert "did not tick" in joined


def test_the_disclaimer_says_what_this_is_not() -> None:
    """Patient-generated, not a diagnosis, not an EHR, and not a triage score."""

    assert DISCLAIMER_TITLE == "Patient-generated, not a diagnosis"
    lowered = DISCLAIMER.lower()
    assert "patient-generated" in lowered
    assert "not a diagnosis" in lowered
    assert "not a clinical assessment" in lowered
    assert "not a medical record" in lowered
    assert "electronic health record" in lowered
    assert "triage" in lowered


def test_the_windows_and_limits_are_the_reviewed_numbers() -> None:
    """Changing one of these changes what a person can share."""

    assert WINDOW_CHOICES == (14, 30, 90)
    assert DEFAULT_WINDOW_DAYS == 30
    assert MAX_NOTE_CHARS == 2000
    assert MAX_QUESTIONS == 10
    assert MAX_QUESTION_CHARS == 300
    assert MAX_THOUGHT_RECORDS == 10
    assert MAX_ASSESSMENTS == 20
    assert MIN_ENTRIES_FOR_DIRECTION == 3
    assert set(DIRECTION_NOTE) == {"higher", "lower", "steady", "not_enough_data"}


# --------------------------------------------------------------------------- #
# the request model
# --------------------------------------------------------------------------- #


def test_a_request_lists_its_items_in_catalogue_order() -> None:
    """Whatever order the client sends, the document is built in one order."""

    request = ShareRequest.model_validate(
        {
            "items": ["questions", "mood_trend", "note"],
            "note": NOTE,
            "questions": list(QUESTIONS),
        }
    )
    assert request.selected == [ShareItem.MOOD_TREND, ShareItem.NOTE, ShareItem.QUESTIONS]


def test_questions_are_trimmed_and_empties_dropped() -> None:
    """A list of blanks is not a list of questions."""

    request = ShareRequest.model_validate(
        {"items": ["questions"], "questions": ["  Why now?  ", "", "   ", "And what next?"]}
    )
    assert request.cleaned_questions == ["Why now?", "And what next?"]


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"items": []}, id="nothing-selected"),
        pytest.param({"items": ["mood_trend", "mood_trend"]}, id="repeated-item"),
        pytest.param({"items": ["mood_trend"], "window_days": 7}, id="window-not-offered"),
        pytest.param({"items": ["mood_trend"], "window_days": 0}, id="window-zero"),
        pytest.param({"items": ["note"]}, id="note-ticked-but-empty"),
        pytest.param({"items": ["note"], "note": "   "}, id="note-only-spaces"),
        pytest.param({"items": ["note"], "note": "x" * (MAX_NOTE_CHARS + 1)}, id="note-too-long"),
        pytest.param({"items": ["mood_trend"], "note": NOTE}, id="note-sent-but-unticked"),
        pytest.param({"items": ["questions"]}, id="questions-ticked-but-empty"),
        pytest.param(
            {"items": ["questions"], "questions": ["a"] * (MAX_QUESTIONS + 1)},
            id="too-many-questions",
        ),
        pytest.param(
            {"items": ["questions"], "questions": ["x" * (MAX_QUESTION_CHARS + 1)]},
            id="question-too-long",
        ),
        pytest.param({"items": ["mood_trend"], "questions": ["Why now?"]}, id="questions-unticked"),
        pytest.param({"items": ["thought_records"]}, id="records-ticked-but-none-chosen"),
        pytest.param(
            {"items": ["thought_records"], "thought_record_ids": [str(uuid.uuid4())] * 2},
            id="records-repeated",
        ),
        pytest.param(
            {
                "items": ["thought_records"],
                "thought_record_ids": [str(uuid.uuid4()) for _ in range(MAX_THOUGHT_RECORDS + 1)],
            },
            id="too-many-records",
        ),
        pytest.param(
            {"items": ["mood_trend"], "thought_record_ids": [str(uuid.uuid4())]},
            id="records-sent-but-unticked",
        ),
        pytest.param({"items": ["mood_trend"], "surprise": 1}, id="unknown-field"),
        pytest.param({"items": ["not_an_item"]}, id="unknown-item"),
    ],
)
def test_a_selection_that_would_widen_the_share_is_refused(body: dict[str, object]) -> None:
    """Every way of asking for more than was ticked fails validation."""

    with pytest.raises(ValidationError):
        ShareRequest.model_validate(body)


# --------------------------------------------------------------------------- #
# the payload
# --------------------------------------------------------------------------- #


def test_the_payload_keeps_its_kind_and_its_banner() -> None:
    """A reader can tell what this document is from the document itself."""

    payload = sample_payload()
    assert payload.kind == "manovia.appointment-summary"
    assert payload.schema_version == 1
    assert payload.app == "Manovia"
    assert payload.disclaimer_title == DISCLAIMER_TITLE
    assert payload.disclaimer == DISCLAIMER
    assert payload.never_included == list(NEVER_INCLUDED)
    assert payload.mood_trend is not None
    assert payload.mood_trend.notes_included is False
    assert all(item.item_answers_included is False for item in payload.assessments or [])
    assert all(item.band_is_a_diagnosis is False for item in payload.assessments or [])


def test_the_payload_serialises_the_same_way_every_time() -> None:
    """The bytes are what gets hashed and encrypted, so they must be stable."""

    payload = sample_payload()
    first = payload.as_json()
    second = SharePayload.model_validate(json.loads(first)).as_json()
    assert first == second
    assert isinstance(first, bytes)
    assert first.decode("utf-8")
    assert payload.as_dict() == json.loads(first)
    # Nothing is dropped just because it is None: a reader can tell "no note"
    # from "the note field is missing".
    assert b'"note"' in sample_payload(note=None).as_json()


def test_consent_lines_are_rendered_from_the_payload() -> None:
    """One input, two outputs: the document and the sentence about it."""

    payload = sample_payload()
    lines = consent_lines(payload)
    joined = "\n".join(lines)
    assert EMAIL_LINE in joined
    assert "6 check-ins over 30 days" in joined
    assert "2 results" in joined
    assert "GAD-7" in joined and "PHQ-9" in joined
    assert (
        "Individual answers are not included" in joined.lower()
        or "individual answers" in joined.lower()
    )
    assert "1 you chose" in joined
    assert "3 completions" in joined
    assert f"Your note: {len(NOTE)} characters" in joined
    assert f"Your questions: {len(QUESTIONS)}" in joined
    assert "Never included:" in joined
    assert lines[-1] == DISCLAIMER
    assert DISCLAIMER_TITLE.lower() in joined.lower() or "not a diagnosis" in joined.lower()


def test_consent_lines_for_a_minimal_selection_say_what_is_missing() -> None:
    """Unticked items do not appear, and an empty share says so."""

    only_note = sample_payload(
        items=[ShareItem.NOTE],
        identity=None,
        mood_trend=None,
        assessments=None,
        thought_records=None,
        exercise_helpfulness=None,
        questions=None,
    )
    lines = consent_lines(only_note)
    assert any("Your note" in line for line in lines)
    assert not any("Mood summary" in line for line in lines)
    assert not any("Screening history" in line for line in lines)
    assert not any("Identifies you" in line for line in lines)

    empty = sample_payload(
        items=[],
        identity=None,
        mood_trend=None,
        assessments=None,
        thought_records=None,
        exercise_helpfulness=None,
        note=None,
        questions=None,
    )
    assert consent_lines(empty)[0] == "Nothing is selected, so nothing would be shared."


def test_an_assessment_with_no_results_still_says_so() -> None:
    """'none' rather than a silently empty list."""

    payload = sample_payload(assessments=[])
    assert any("none" in line for line in consent_lines(payload))


# --------------------------------------------------------------------------- #
# build_payload, against the database
# --------------------------------------------------------------------------- #


async def add_check_ins(
    session: AsyncSession,
    user: User,
    offsets_and_valence: list[tuple[int, int]],
    *,
    now: datetime = NOW,
) -> None:
    """Mood check-ins at fixed day offsets, so the trend arithmetic is exact."""

    for offset, valence in offsets_and_valence:
        session.add(
            MoodEntry(
                user_id=user.id,
                valence=valence,
                energy=3,
                emotions=["calm"],
                factors={"sleep": 1},
                created_at=now - timedelta(days=offset),
            )
        )
    await session.commit()


@pytest.mark.asyncio
async def test_the_mood_trend_needs_three_check_ins_in_each_half(
    session: AsyncSession, demo_user: User
) -> None:
    """With less than that, the payload refuses to use a direction word."""

    await add_check_ins(session, demo_user, [(28, 1), (25, 2), (2, 5)])
    payload = await build_payload(
        session,
        demo_user,
        ShareRequest.model_validate({"items": ["mood_trend"], "window_days": 30}),
        now=NOW,
    )
    trend = payload.mood_trend
    assert trend is not None
    assert trend.entries == 3
    assert trend.direction == "not_enough_data"
    assert trend.direction_note == DIRECTION_NOTE["not_enough_data"]
    assert trend.notes_included is False
    assert trend.mean_valence == pytest.approx(2.67)
    assert payload.counts["mood_check_ins"] == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("early", "recent", "expected"),
    [
        pytest.param([1, 1, 2], [4, 4, 5], "higher", id="clearly-higher"),
        pytest.param([4, 4, 5], [1, 1, 2], "lower", id="clearly-lower"),
        # Four check-ins per half: a 0.25 mean difference is exactly the epsilon,
        # and the epsilon is a floor for claiming a direction, so this is "steady".
        pytest.param([3, 3, 3, 3], [3, 3, 3, 4], "steady", id="inside-the-epsilon"),
    ],
)
async def test_the_direction_word_follows_the_data(
    session: AsyncSession,
    demo_user: User,
    early: list[int],
    recent: list[int],
    expected: str,
) -> None:
    """First half versus second half, with a minimum difference to claim anything."""

    await add_check_ins(
        session,
        demo_user,
        [(28 - index, value) for index, value in enumerate(early)]
        + [(6 - index, value) for index, value in enumerate(recent)],
    )
    payload = await build_payload(
        session,
        demo_user,
        ShareRequest.model_validate({"items": ["mood_trend"], "window_days": 30}),
        now=NOW,
    )
    assert payload.mood_trend is not None
    assert payload.mood_trend.direction == expected
    assert payload.mood_trend.direction_note == DIRECTION_NOTE[expected]


@pytest.mark.asyncio
async def test_the_window_bounds_the_mood_summary(session: AsyncSession, demo_user: User) -> None:
    """A 14-day window does not report a check-in from a month ago."""

    await add_check_ins(session, demo_user, [(40, 1), (20, 2), (5, 4), (1, 5)])
    payload = await build_payload(
        session,
        demo_user,
        ShareRequest.model_validate({"items": ["mood_trend"], "window_days": 14}),
        now=NOW,
    )
    trend = payload.mood_trend
    assert trend is not None
    assert trend.entries == 2
    assert trend.window_days == 14
    assert trend.window_start == NOW - timedelta(days=14)


@pytest.mark.asyncio
async def test_assessments_are_the_full_history_with_their_verified_codes(
    session: AsyncSession, demo_user: User
) -> None:
    """Newest first, totals and bands only, each with the LOINC term it claims."""

    from app.models.assessment import AssessmentResult

    for instrument, total, band, offset in (
        (AssessmentInstrument.PHQ9, 12, AssessmentBand.MODERATE, 8),
        (AssessmentInstrument.GAD7, 9, AssessmentBand.MILD, 7),
        (AssessmentInstrument.PHQ9, 4, AssessmentBand.MINIMAL, 40),
    ):
        session.add(
            AssessmentResult(
                user_id=demo_user.id,
                instrument=instrument,
                answers={"q1": 1},
                total=total,
                band=band,
                created_at=NOW - timedelta(days=offset),
            )
        )
    await session.commit()

    payload = await build_payload(
        session,
        demo_user,
        ShareRequest.model_validate({"items": ["assessments"], "window_days": 14}),
        now=NOW,
    )
    assessments = payload.assessments or []
    assert len(assessments) == 3, "the item is the history; the window does not cut it"
    assert [item.total for item in assessments] == [9, 12, 4]
    assert [item.loinc_code for item in assessments] == ["70274-6", "44261-6", "44261-6"]
    assert all(item.loinc_source_url.startswith("https://loinc.org/") for item in assessments)
    assert all(item.ucum_unit == "{score}" for item in assessments)
    assert [item.band_label for item in assessments] == ["Mild", "Moderate", "Minimal"]
    assert all(item.item_answers_included is False for item in assessments)


@pytest.mark.asyncio
async def test_exercise_helpfulness_only_calls_an_improvement_helpful(
    session: AsyncSession, demo_user: User
) -> None:
    """A rating that went down is never named as what helped most."""

    from app.models.exercise_completion import ExerciseCompletion

    rows = [
        ("Box breathing", 2, 4),  # improved by 2
        ("Body scan", 4, 3),  # got worse
        ("Body scan", 4, 3),
        ("Grounding", 3, None),  # unrated afterwards
    ]
    for index, (title, pre, post) in enumerate(rows):
        session.add(
            ExerciseCompletion(
                user_id=demo_user.id,
                exercise_id=title.lower().replace(" ", "-"),
                exercise_title=title,
                definition_sha256="0" * 64,
                duration_seconds=180,
                pre_rating=pre,
                post_rating=post,
                created_at=NOW - timedelta(days=index + 1),
            )
        )
    await session.commit()

    payload = await build_payload(
        session,
        demo_user,
        ShareRequest.model_validate({"items": ["exercise_helpfulness"], "window_days": 30}),
        now=NOW,
    )
    summary = payload.exercise_helpfulness
    assert summary is not None
    assert summary.completions == 4
    assert summary.rated_completions == 3
    assert summary.most_helpful == "Box breathing"
    by_title = {row.title: row for row in summary.exercises}
    assert by_title["Box breathing"].mean_change == pytest.approx(2.0)
    assert by_title["Body scan"].mean_change == pytest.approx(-1.0)
    assert by_title["Grounding"].mean_change is None
    assert "1 (very low) to 5 (very good)" in summary.rating_scale
    assert "association" in summary.change_caveat


@pytest.mark.asyncio
async def test_no_improvement_anywhere_means_nothing_is_called_helpful(
    session: AsyncSession, demo_user: User
) -> None:
    """The honest answer is silence, not the least bad exercise."""

    from app.models.exercise_completion import ExerciseCompletion

    for index, (pre, post) in enumerate([(4, 2), (3, 3)]):
        session.add(
            ExerciseCompletion(
                user_id=demo_user.id,
                exercise_id="body-scan",
                exercise_title="Body scan",
                definition_sha256="0" * 64,
                duration_seconds=180,
                pre_rating=pre,
                post_rating=post,
                created_at=NOW - timedelta(days=index + 1),
            )
        )
    await session.commit()

    payload = await build_payload(
        session,
        demo_user,
        ShareRequest.model_validate({"items": ["exercise_helpfulness"], "window_days": 30}),
        now=NOW,
    )
    assert payload.exercise_helpfulness is not None
    assert payload.exercise_helpfulness.most_helpful is None
    assert payload.exercise_helpfulness.completions == 2


@pytest.mark.asyncio
async def test_an_unticked_item_is_absent_and_an_unknown_record_is_an_error(
    session: AsyncSession, demo_user: User
) -> None:
    """The payload contains exactly the ticked items — no more, no silent less."""

    await add_check_ins(session, demo_user, [(3, 4)])
    payload = await build_payload(
        session,
        demo_user,
        ShareRequest.model_validate(
            {"items": ["mood_trend", "questions"], "window_days": 30, "questions": ["Why now?"]}
        ),
        now=NOW,
    )
    assert payload.items == [ShareItem.MOOD_TREND, ShareItem.QUESTIONS]
    assert payload.mood_trend is not None
    assert payload.assessments is None
    assert payload.thought_records is None
    assert payload.exercise_helpfulness is None
    assert payload.note is None
    assert payload.identity is None
    assert payload.questions == ["Why now?"]
    assert payload.counts == {"mood_check_ins": 1, "questions": 1}

    with pytest.raises(ShareContentError):
        await build_payload(
            session,
            demo_user,
            ShareRequest.model_validate(
                {
                    "items": ["thought_records"],
                    "thought_record_ids": [str(uuid.uuid4())],
                }
            ),
            now=NOW,
        )


@pytest.mark.asyncio
async def test_an_anonymous_account_says_so_instead_of_inventing_an_identity(
    session: AsyncSession,
) -> None:
    """A guest has no email; the document must not imply one."""

    from app.db.repos import UserRepository

    guest = await UserRepository(session).create(is_anonymous=True)
    await session.commit()
    payload = await build_payload(
        session, guest, ShareRequest.model_validate({"items": ["identity"]}), now=NOW
    )
    assert payload.identity is not None
    assert "Anonymous" in payload.identity
    assert "@" not in payload.identity
    assert payload.counts["identity"] == 0
    assert "Identifies you as:" in "\n".join(consent_lines(payload))
