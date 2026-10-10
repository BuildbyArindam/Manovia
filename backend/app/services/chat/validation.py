"""Step 1a — is this a message at all?

Length, encoding and shape, checked before anything reads the text. The checks
are cheap and strict because everything downstream (the rules engine, the ML
model, the emotion model, the LLM) is more expensive and less predictable than
a ``str`` that has been checked.

Rejections are **curated 422s with a stable code**, never an echo of the input.
The text is Unicode-normalised to NFC (so ``é`` typed two ways is one string to
the safety patterns) and otherwise left exactly as written: no trimming of
interior whitespace, no case folding, no stripping of punctuation. The safety
engine does its own normalisation on its own copy.
"""

from __future__ import annotations

import unicodedata
from typing import Final

from app.core.errors import ApiError

#: Control characters people legitimately type or paste.
_ALLOWED_CONTROLS: Final = frozenset({"\n", "\r", "\t"})


def validate_message(text: str, *, max_chars: int) -> str:
    """Return the NFC-normalised message or raise a curated 422.

    Codes: ``message_empty``, ``message_too_long``, ``message_encoding``.
    """
    # Lone surrogates survive JSON ("\\ud800") but are not text: they cannot be
    # encoded, so they could not be stored, hashed or sent to a provider.
    try:
        text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ApiError(
            422,
            "message_encoding",
            "That message contains characters I can't read. Please try typing it again.",
        ) from exc

    normalised = unicodedata.normalize("NFC", text)

    for char in normalised:
        if char in _ALLOWED_CONTROLS:
            continue
        category = unicodedata.category(char)
        # Cc = control (NUL, ESC, …): binary or corrupted input rather than
        # something a person wrote. (Unassigned code points are *not* refused:
        # an emoji newer than this interpreter's Unicode tables is still an emoji.)
        if category == "Cc":
            raise ApiError(
                422,
                "message_encoding",
                "That message contains characters I can't read. Please try typing it again.",
            )

    if not normalised.strip():
        raise ApiError(422, "message_empty", "Write a few words and send again.")

    if len(normalised) > max_chars:
        raise ApiError(
            422,
            "message_too_long",
            f"That message is longer than {max_chars} characters. "
            "Could you send it in smaller parts?",
        )
    return normalised
