"""PII redaction: typed placeholders out, nothing identifiable over the wire.

Manovia's whole promise is that a person can say something difficult and have
it go nowhere. Encryption at rest protects the database; this module protects
the one place the text *must* leave the machine — the LLM call.

What it removes
---------------
=========================  =====================================================
placeholder                what it replaces
=========================  =====================================================
``[EMAIL]``                email addresses
``[PHONE]``                Indian (+91 / 0-prefixed / bare 10-digit starting
                           6-9) and international (``+CC ...``, ``(415) 555-0132``)
``[ID]``                   Aadhaar-like (12 digits), PAN-like (ABCDE1234F),
                           SSN-like (123-45-6789), and generic long digit runs
``[CARD]``                 credit-card-like numbers that pass the Luhn check
``[URL]``                  any URL, and anything with a secret-looking query
                           parameter (``token=``, ``key=``, ``otp=``, ...)
``[PERSON]``               person names — **off by default**, see below
=========================  =====================================================

Design decisions worth defending
--------------------------------
**Over-redaction is the safe direction.** A 10-digit order number becomes
``[ID]`` and the model never sees it. Losing a detail the model did not need is
free; leaking one it did not need is not recoverable.

**Order matters, and it is fixed.** Card → ID → phone → email → URL. A 16-digit
card is also 16 digits that a naive phone regex would happily take two of; a
12-digit Aadhaar is a phone number plus two digits. The patterns are applied
longest-and-strictest first so the *most specific* label wins.

**URLs with secrets are replaced whole, not scrubbed.** ``https://x.test/cb?code=abc``
becomes ``[URL]``. Rewriting it to ``https://x.test/cb?code=[REDACTED]`` would
still tell the model where the user's account lives.

**Names are opt-in.** ``[PERSON]`` needs an NER model; the default
:class:`NullNameFinder` finds nothing, because a false positive on a common
Indian given name (which is often also an ordinary word — *Kiran*, *Jyoti*,
*Anand*) mangles a sentence, and a name detector that is wrong half the time
teaches engineers to switch redaction off. Enable ``LLM_REDACT_NAMES=true``
with a model behind :class:`NameFinder` when one is available.

**The reversible map never leaves the request.** :attr:`RedactionResult.mapping`
exists for the one legitimate case — putting a person's own words back in text
that never left the process. The chain discards it; nothing persists it; it is
never logged, and :meth:`Redactor.restore` is the only way to use it.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

# --- Placeholders ------------------------------------------------------------

EMAIL = "[EMAIL]"
PHONE = "[PHONE]"
ID = "[ID]"
CARD = "[CARD]"
URL = "[URL]"
PERSON = "[PERSON]"

#: Every placeholder this module can emit. Anything outside this set reaching a
#: provider payload is a bug, and a test asserts exactly that.
PLACEHOLDERS: Final[tuple[str, ...]] = (EMAIL, PHONE, ID, CARD, URL, PERSON)

#: Query-string keys whose *presence* is itself a secret. A URL carrying any of
#: these is replaced whole rather than trimmed.
SECRET_QUERY_KEYS: Final[frozenset[str]] = frozenset(
    {
        "token",
        "access_token",
        "refresh_token",
        "id_token",
        "api_key",
        "apikey",
        "key",
        "secret",
        "client_secret",
        "password",
        "passwd",
        "pwd",
        "otp",
        "code",
        "auth",
        "authorization",
        "session",
        "sessionid",
        "session_id",
        "sid",
        "sig",
        "signature",
        "reset",
        "reset_token",
        "verification",
        "verification_token",
        "magic",
        "magic_link",
        "invite",
        "jwt",
        "bearer",
        "state",
        "nonce",
    }
)

# --- Patterns ----------------------------------------------------------------

# A URL: scheme:// ... or a bare www. host. Deliberately first-class, because
# "here is the link to my medical records" is a real thing people paste.
_URL_RE: Final = re.compile(
    r"""
    (?:
        \b[a-zA-Z][a-zA-Z0-9+.\-]*://[^\s<>"')\]]+
      | \bwww\.[^\s<>"')\]]+
    )
    """,
    re.VERBOSE,
)

# Email. The local part is deliberately permissive (RFC 5322 allows
# !#$%&'*+/=?^_`{|}~.- in it): this is a redactor, not a validator, and a
# missed address is the one failure mode that cannot be undone.
_EMAIL_RE: Final = re.compile(
    r"""
    [A-Za-z0-9!#$%&'*+/=?^_`{|}~.\-]+
    @
    [A-Za-z0-9](?:[A-Za-z0-9\-]*[A-Za-z0-9])?
    (?:\.[A-Za-z0-9](?:[A-Za-z0-9\-]*[A-Za-z0-9])?)*
    \.[A-Za-z]{2,}
    """,
    re.VERBOSE,
)

# Payment cards: 13-19 digits, optionally separated by spaces or hyphens in
# 4-digit groups. Luhn-checked in code before the label is applied.
_CARD_CANDIDATE_RE: Final = re.compile(r"(?<![\d\-])(?:\d[ \-]?){12,18}\d(?![\d\-])")

# Aadhaar: 12 digits, optionally as 4-4-4 groups. Not 10, not 16.
_AADHAAR_RE: Final = re.compile(r"(?<!\d)(?:\d{4}[ \-]?\d{4}[ \-]?\d{4})(?!\d)")

# PAN (India): five letters, four digits, one letter. Case-insensitive; the
# letters are upper-cased by convention but people type them either way.
_PAN_RE: Final = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]{5}\d{4}[A-Za-z](?![A-Za-z0-9])")

# US SSN: 123-45-6789 or 123 45 6789. Nine bare digits are NOT treated as an
# SSN — that would swallow order numbers, PIN codes and every long integer.
_SSN_RE: Final = re.compile(r"(?<!\d)\d{3}[ \-]\d{2}[ \-]\d{4}(?!\d)")

# International: +CC followed by 6-15 digits with common separators.
_INTL_PHONE_RE: Final = re.compile(
    r"(?<![\w+])\+\d{1,3}[\s\-.]?(?:\(?\d{1,4}\)?[\s\-.]?){1,4}\d{2,4}(?!\d)"
)

# Indian, no country code: a leading 0 (STD code) or a bare mobile starting
# 6-9. Separators optional; a space-separated 5-5 form is common offline.
_IN_PHONE_RE: Final = re.compile(
    r"""
    (?<![\d+])
    (?:
        0\d{2,5}[\s\-.]?\d{5,8}
      | [6-9]\d[\s\-.]?\d{4}[\s\-.]?\d{4}
      | [6-9]\d{9}
    )
    (?!\d)
    """,
    re.VERBOSE,
)

# Generic international-style grouping: (415) 555-0132 / 415-555-0132.
_GROUPED_PHONE_RE: Final = re.compile(
    r"(?<![\d(])(?:\(\d{2,4}\)[\s\-.]?|\d{2,4}[\s\-.]){2,4}\d{2,4}(?!\d)"
)

# Any long digit run that survived everything above. Long, not short: a
# 6-digit OTP is a code, a 14-digit run is an account number. No upper bound
# and no look-around: a 23-digit run must not slip through because it is too
# long for the 9-19 window a card would fit in.
_LONG_DIGITS_RE: Final = re.compile(r"\d{9,}")

# --- Luhn --------------------------------------------------------------------


def luhn_ok(digits: str) -> bool:
    """True when ``digits`` passes the Luhn checksum (every real card does).

    Used to tell a card number from a random 16-digit run: only Luhn-valid runs
    are labelled ``[CARD]``, and the rest fall through to ``[ID]``. Roughly one
    in ten random runs passes by chance, which is the right trade — a missed
    card is worse than a mislabelled one, and both are redacted anyway.
    """
    cleaned = re.sub(r"\D", "", digits)
    if not 12 <= len(cleaned) <= 19 or not cleaned.isdigit():
        return False
    total = 0
    for index, char in enumerate(reversed(cleaned)):
        value = int(char)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def _has_secret_query(url: str) -> bool:
    """Whether a URL carries a credential-ish query parameter."""
    _, _, rest = url.partition("?")
    if not rest:
        return False
    for pair in re.split(r"[&#]", rest):
        key, _, _ = pair.partition("=")
        if key.strip().casefold() in SECRET_QUERY_KEYS:
            return True
    return False


# --- Results -----------------------------------------------------------------


@dataclass(frozen=True)
class RedactionHit:
    """One thing that was removed."""

    placeholder: str
    start: int
    end: int

    @property
    def length(self) -> int:
        return self.end - self.start


@dataclass(frozen=True)
class RedactionResult:
    """The redacted text plus what was taken out.

    ``mapping`` is the reversible map. It is **request memory only**: holding it
    lets a caller put the original words back into text that never left the
    process, and nothing else. It is not persisted, not logged, and not
    serialised into anything that outlives the request.
    """

    text: str
    hits: tuple[RedactionHit, ...] = ()
    mapping: dict[str, str] = field(default_factory=dict)

    @property
    def is_clean(self) -> bool:
        return not self.hits

    @property
    def counts(self) -> dict[str, int]:
        """Placeholder → how many times it was used."""
        out: dict[str, int] = {}
        for hit in self.hits:
            out[hit.placeholder] = out.get(hit.placeholder, 0) + 1
        return out

    @property
    def placeholders_used(self) -> tuple[str, ...]:
        return tuple(sorted({hit.placeholder for hit in self.hits}))

    def summary(self) -> str:
        """A loggable one-liner. Counts only — never the originals."""
        if not self.hits:
            return "none"
        return ", ".join(f"{name}x{count}" for name, count in sorted(self.counts.items()))


# --- Name detection (optional) -----------------------------------------------


class NameFinder:
    """Interface for the optional person-name pass (``[PERSON]``)."""

    name: str = "null"

    def find(self, text: str) -> list[tuple[int, int]]:
        """Character spans of person names in ``text``."""
        return []


class NullNameFinder(NameFinder):
    """The default: finds nothing.

    Names stay un-redacted until an NER model is wired in deliberately (see the
    module docstring for why the default is not "best effort").
    """

    name = "null"

    def find(self, text: str) -> list[tuple[int, int]]:
        return []


class SpacyNameFinder(NameFinder):
    """Adapter for a spaCy NER model, lazily imported and never required.

    Constructing this does not import spaCy; the first :meth:`find` does. A
    missing library or an unavailable model degrades to *no* name redaction
    with one warning, never to a failed request.
    """

    name = "spacy"

    def __init__(self, model: str = "en_core_web_sm") -> None:
        self._model_name = model
        self._nlp: Any | None = None
        self._unavailable = False

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def is_loaded(self) -> bool:
        return self._nlp is not None

    def _load(self) -> Any | None:
        if self._nlp is not None or self._unavailable:
            return self._nlp
        try:
            import spacy
        except ImportError:  # pragma: no cover - spaCy is an optional extra
            self._unavailable = True
            return None
        try:
            self._nlp = spacy.load(self._model_name)
        except Exception:  # pragma: no cover - model not downloaded
            self._unavailable = True
            return None
        return self._nlp

    def find(self, text: str) -> list[tuple[int, int]]:
        nlp = self._load()
        if nlp is None:
            return []
        doc = nlp(text)
        spans: list[tuple[int, int]] = []
        for ent in getattr(doc, "ents", ()):  # pragma: no cover - needs spaCy
            if getattr(ent, "label_", "") == "PERSON":
                spans.append((ent.start_char, ent.end_char))
        return spans


# --- The redactor ------------------------------------------------------------


def _digits_len(candidate: str) -> int:
    return len(re.sub(r"\D", "", candidate))


class Redactor:
    """Removes PII from text, replacing it with typed placeholders."""

    def __init__(
        self,
        *,
        name_finder: NameFinder | None = None,
        redact_names: bool = False,
        on_hit: Callable[[RedactionHit], None] | None = None,
    ) -> None:
        self._name_finder = name_finder or NullNameFinder()
        self._redact_names = redact_names
        self._on_hit = on_hit

    @property
    def redact_names(self) -> bool:
        return self._redact_names

    @property
    def name_finder(self) -> NameFinder:
        return self._name_finder

    def redact(self, text: str) -> RedactionResult:
        """Return the text with every recognised identifier replaced.

        The empty string and ``None``-ish input are handled rather than raising:
        this runs on the path to a person who is asking for help, and an
        exception here is a failed conversation.
        """
        if not text:
            return RedactionResult(text=text or "")
        spans: list[tuple[int, int, str]] = []
        _collect(_URL_RE, text, URL, spans, guard=_url_worth_redacting)
        _collect(_EMAIL_RE, text, EMAIL, spans)
        # Card-like runs are labelled [CARD] when the checksum proves it and
        # [ID] when it does not: either way the whole run goes, so a
        # 16-digit account number cannot leave four digits behind by failing
        # a test it was never meant to pass.
        _collect(_CARD_CANDIDATE_RE, text, CARD, spans, guard=luhn_ok)
        _collect(_CARD_CANDIDATE_RE, text, ID, spans, guard=_long_non_luhn)
        _collect(_AADHAAR_RE, text, ID, spans)
        _collect(_PAN_RE, text, ID, spans)
        _collect(_SSN_RE, text, ID, spans)
        _collect(_INTL_PHONE_RE, text, PHONE, spans, guard=_looks_like_phone)
        _collect(_IN_PHONE_RE, text, PHONE, spans)
        _collect(_GROUPED_PHONE_RE, text, PHONE, spans, guard=_looks_like_phone)
        _collect(_LONG_DIGITS_RE, text, ID, spans)
        if self._redact_names:
            for start, end in self._name_finder.find(text):
                spans.append((start, end, PERSON))

        resolved = _resolve_overlaps(spans)
        if not resolved:
            return RedactionResult(text=text)

        hits: list[RedactionHit] = []
        mapping: dict[str, str] = {}
        pieces: list[str] = []
        cursor = 0
        counters: dict[str, int] = {}
        for start, end, placeholder in resolved:
            original = text[start:end]
            pieces.append(text[cursor:start])
            pieces.append(placeholder)
            cursor = end
            counters[placeholder] = counters.get(placeholder, 0) + 1
            hit = RedactionHit(placeholder=placeholder, start=start, end=end)
            hits.append(hit)
            # First occurrence of each placeholder owns the reverse map entry;
            # later ones overwrite, and that is fine — the map exists so a
            # caller can restore the *last* thing it sent, not an audit trail.
            mapping[placeholder] = original
            if self._on_hit is not None:
                self._on_hit(hit)
        pieces.append(text[cursor:])
        return RedactionResult(text="".join(pieces), hits=tuple(hits), mapping=dict(mapping))

    def redact_many(self, texts: Iterable[str]) -> list[RedactionResult]:
        return [self.redact(text) for text in texts]

    def restore(self, text: str, result: RedactionResult) -> str:
        """Put the originals back into ``text`` using ``result``'s map.

        Only for text that never left the process. Restoring into anything that
        is about to be stored, logged or sent defeats the entire module.
        """
        if not result.mapping:
            return text
        restored = text
        for placeholder, original in result.mapping.items():
            restored = restored.replace(placeholder, original)
        return restored

    def describe(self) -> dict[str, object]:
        return {
            "placeholders": list(PLACEHOLDERS),
            "redact_names": self._redact_names,
            "name_finder": self._name_finder.name,
        }


def _url_worth_redacting(candidate: str) -> bool:
    """Every URL is redacted; the query check exists for future narrowing.

    Kept as a named guard so the policy ("all URLs") lives in one line rather
    than being implied by the absence of a check.
    """
    _ = _has_secret_query(candidate)  # documented, currently informational
    return True


def _long_non_luhn(candidate: str) -> bool:
    """A 13-19 digit run that is not a valid card — still an identifier."""
    return _digits_len(candidate) >= 13 and not luhn_ok(candidate)


def _looks_like_phone(candidate: str) -> bool:
    """Reject digit runs that a phone regex matched but cannot be a number."""
    digits = re.sub(r"\D", "", candidate)
    return 7 <= len(digits) <= 15


def _collect(
    pattern: re.Pattern[str],
    text: str,
    placeholder: str,
    out: list[tuple[int, int, str]],
    *,
    guard: Callable[[str], bool] | None = None,
) -> None:
    """Record every match of ``pattern``, appending to ``out``."""
    for match in pattern.finditer(text):
        candidate = match.group(0)
        if guard is not None and not guard(candidate):
            continue
        out.append((match.start(), match.end(), placeholder))


def _resolve_overlaps(spans: Sequence[tuple[int, int, str]]) -> list[tuple[int, int, str]]:
    """Drop overlaps, longest span winning, then sort by position.

    Two patterns can match the same characters (a 12-digit Aadhaar is also a
    phone candidate plus two digits). Taking the longest keeps the most
    specific label; ties are broken by the order the patterns ran in, which is
    fixed, so the result never depends on set iteration.
    """
    if not spans:
        return []
    ordered = sorted(spans, key=lambda item: (-(item[1] - item[0]), item[0]))
    kept: list[tuple[int, int, str]] = []
    for start, end, placeholder in ordered:
        if any(start < kept_end and kept_start < end for kept_start, kept_end, _ in kept):
            continue
        kept.append((start, end, placeholder))
    kept.sort(key=lambda item: item[0])
    return kept


#: The process-wide default redactor: PII on, names off. Cheap to construct
#: (compiled patterns are module-level) so callers may also build their own.
DEFAULT_REDACTOR: Final = Redactor()
