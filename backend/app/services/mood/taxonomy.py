"""What a check-in may contain: the emotion taxonomy, the factor vocabulary, limits.

A check-in is deliberately small. Ten seconds of somebody's day buys two 1-5
scales, at most three emotion words and at most five context tags — and every one
of those values comes from a **closed** vocabulary that a human wrote down and
can review. Nothing here accepts free text, so nothing here can drift into a
diagnosis, a score, or a word somebody's mood cannot be reduced to.

Two sources of truth, on purpose:

* **Emotions** are the nine-label taxonomy the rest of the product already
  speaks (:data:`app.services.nlp.base.EMOTIONS`). A check-in choosing "sadness"
  and a chat message analysed as "sadness" mean the same thing, which is what
  makes the two surfaces comparable later. They are re-exported rather than
  copied; ``tests/unit/test_mood_taxonomy.py`` fails if that ever diverges.
* **Factors** are the situational tags: what was going on, in the person's own
  life, not in their head. They are context for a pattern ("I sleep badly on
  work days"), never a cause and never a judgement of the person's coping.

Both vocabularies are wire values (lowercase ASCII), so a UI can translate them
without a lookup table in the database and a rename in one language cannot change
stored data.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from app.services.nlp.base import EMOTIONS, NEUTRAL

#: The emotion tags a check-in may carry. Same nine labels as the analyser, no
#: more: somebody who wants a word that is not here should write it in the note,
#: where it is theirs, encrypted, and not something the app claims to measure.
EMOTION_TAGS: Final[tuple[str, ...]] = EMOTIONS

#: The word used when someone quite reasonably feels nothing in particular. It is
#: a real option, not a fallback: "flat" is information.
NEUTRAL_TAG: Final = NEUTRAL

#: What might have been going on around the feeling. Chosen to be things people
#: genuinely notice and can act on, and to avoid anything that reads as a
#: verdict on how they live (no "diet", no "exercise", no "screen time").
#:
#: Deliberately not generic: a "health" tag on a mental-health product covers
#: being unwell, which is context — not a lifestyle score.
MOOD_FACTORS: Final[tuple[str, ...]] = (
    "sleep",
    "study",
    "work",
    "family",
    "friends",
    "health",
    "money",
    "weather",
)

#: A check-in is short by design; these caps keep it short and keep the payload
#: boring. Three emotions is roughly "what I notice first"; five factors is
#: roughly "what today was".
MAX_EMOTIONS: Final = 3
MAX_FACTORS: Final = 5

#: The optional note. Long enough for a paragraph, short enough that it is still
#: a note — and every character of it is encrypted at rest and screened by the
#: safety engine before it is stored.
NOTE_MAX_CHARS: Final = 1000

#: The two scales, as (min, max). Named so the API, the validation messages and
#: the frontend all speak about the same numbers.
SCALE_MIN: Final = 1
SCALE_MAX: Final = 5


#: The severity recorded for a factor that was simply chosen. The database column
#: is a mapping (see :mod:`app.models.mood`) so an intensity can arrive later
#: without a migration; today "present" is 1 and nothing else writes to it.
FACTOR_PRESENT: Final = 1


def factors_document(tags: tuple[str, ...] | list[str]) -> dict[str, object]:
    """Wrap chosen factor tags in the mapping the column stores.

    Deliberately *not* a list: the schema models a factor as name → severity, and
    widening the API to a list of tags must not throw that away. Order is kept by
    the insertion order of the mapping, which is how the tags a person picked come
    back in the same order they picked them.
    """
    return {tag: FACTOR_PRESENT for tag in tags}


def factors_from_document(document: Mapping[str, object] | None) -> list[str]:
    """The tags in a stored factors document, in their original order."""
    return list(document.keys()) if document else []


def unknown_emotions(values: tuple[str, ...] | list[str]) -> list[str]:
    """The values that are not in :data:`EMOTION_TAGS`, in the order given."""
    known = set(EMOTION_TAGS)
    return [value for value in values if value not in known]


def unknown_factors(values: tuple[str, ...] | list[str]) -> list[str]:
    """The values that are not in :data:`MOOD_FACTORS`, in the order given."""
    known = set(MOOD_FACTORS)
    return [value for value in values if value not in known]
