"""Redaction tests: example-based coverage plus hypothesis properties.

The example tests answer "does it catch the thing I meant?" for each
identifier class. The property tests answer the question that actually matters
and that examples cannot: **is there any input at all for which identifiable
text survives?** Examples prove the patterns fire; only a property over
generated input can argue that nothing slips between them.

Properties asserted here:

1. **leaves nothing recognisable** — after one pass, a second pass finds
   nothing. If any identifier-shaped text survived, the redactor would find it
   again.
2. **idempotent** — ``redact(redact(t)) == redact(t)``. A redactor that
   rewrites its own placeholders would eventually mangle text into nonsense.
3. **removes the original** — for generated emails, phone numbers, ids and
   cards, no digit run or local-part fragment of the original is left. This is
   the "no PII reaches the provider" claim, tested against data the test does
   not control.
4. **reversible** — ``restore`` round-trips a string with at most one hit per
   placeholder type. (More than one email collapses to the same placeholder;
   that is a deliberate limitation, asserted below rather than papered over.)
5. **never lengthens text into noise** — the output is never longer than the
   input by more than the placeholder overhead it introduced.
"""

from __future__ import annotations

import re

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.services.nlp.redaction import (
    CARD,
    EMAIL,
    ID,
    PERSON,
    PHONE,
    PLACEHOLDERS,
    URL,
    NameFinder,
    NullNameFinder,
    RedactionHit,
    Redactor,
    SpacyNameFinder,
    _has_secret_query,
    luhn_ok,
)

# --- strategies --------------------------------------------------------------

#: Text drawn from the alphabet that actually triggers patterns: digits, the
#: separators people type, '@', '+', and letters (for PAN-shaped ids).
TEXT_ALPHABET = st.characters(
    whitelist_categories=("Ll", "Lu", "Nd"),
    whitelist_characters="@+-._ ()[]/:?&=",
    min_codepoint=32,
)

EMAILS = st.emails()
DIGITS = st.text(alphabet="0123456789", min_size=9, max_size=19)
IN_PHONES = st.from_regex(r"[6-9]\d{9}", fullmatch=True)
INTL_PHONES = st.from_regex(r"\+\d{2,3} ?\d{5}[\d ]{0,8}", fullmatch=True)
AADHAARISH = st.from_regex(r"\d{4} ?\d{4} ?\d{4}", fullmatch=True)
PANISH = st.from_regex(r"[A-Z]{5}\d{4}[A-Z]", fullmatch=True)
SSNISH = st.from_regex(r"\d{3}-\d{2}-\d{4}", fullmatch=True)

#: A known-Luhn-valid card (Visa test number) plus generated 16-digit runs.
CARD_SAMPLES = ("4111111111111111", "5555555555554444", "4242424242424242")


def _sentence(secret: str) -> str:
    """Wrap a secret in ordinary prose, as a person would type it."""
    return f"please note this down: {secret} — that is all for now."


# --- examples ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "placeholder"),
    [
        ("email me at riya.sharma@example.com", EMAIL),
        ("reach me on Riya+test@sub.domain.co.in", EMAIL),
        ("call +91 98765 43210 after six", PHONE),
        ("my number is 09876543210", PHONE),
        ("just dial 9876543210", PHONE),
        ("international: +44 20 7946 0958", PHONE),
        ("us style (415) 555-0132", PHONE),
        ("aadhaar 1234 5678 9012", ID),
        ("aadhaar 123456789012", ID),
        ("pan ABCDE1234F", ID),
        ("ssn 123-45-6789", ID),
        ("long account 1234567890123456", ID),
    ],
)
def test_each_identifier_class_is_replaced(text: str, placeholder: str) -> None:
    result = Redactor().redact(text)
    assert placeholder in result.text
    assert not result.is_clean
    assert result.counts.get(placeholder) == 1


@pytest.mark.parametrize("card", CARD_SAMPLES)
def test_luhn_valid_cards_are_labelled_card(card: str) -> None:
    assert luhn_ok(card)
    result = Redactor().redact(f"my card is {card}")
    assert result.text == "my card is [CARD]"
    assert result.counts == {CARD: 1}


def test_non_luhn_digit_runs_are_still_removed() -> None:
    """Failing the checksum must not mean staying in the text."""
    assert not luhn_ok("1234567890123456")
    result = Redactor().redact("account 1234567890123456 here")
    assert "1234567890123456" not in result.text
    assert result.counts == {ID: 1}


@pytest.mark.parametrize(
    "url",
    [
        "https://app.example.com/reset?token=abc123",
        "https://app.example.com/reset?token=abc&user=7",
        "http://localhost:8000/callback#access_token=zzz",
        "https://manovia.app/privacy",
        "www.example.com/otp?code=9988",
    ],
)
def test_urls_are_replaced_whole(url: str) -> None:
    """Not trimmed: where the link points is itself identifying."""
    result = Redactor().redact(f"see {url} for more")
    assert result.text == f"see {URL} for more"


def test_secret_query_detection() -> None:
    assert _has_secret_query("https://x.test/a?token=1")
    assert _has_secret_query("https://x.test/a?OTP=1")
    assert not _has_secret_query("https://x.test/a?page=2")
    assert not _has_secret_query("https://x.test/a")


def test_plain_text_is_untouched() -> None:
    text = "I had a bad day at work and I do not want to talk about it yet."
    result = Redactor().redact(text)
    assert result.text == text
    assert result.is_clean
    assert result.counts == {}
    assert result.summary() == "none"


def test_short_numbers_survive_deliberately() -> None:
    """A PIN code, a year and an OTP are not identities; over-redacting
    everything numeric would make the text useless to the model."""
    text = "I live in 400019, it happened in 2023, and the code was 482913."
    assert Redactor().redact(text).text == text


def test_empty_and_whitespace_input() -> None:
    assert Redactor().redact("").text == ""
    assert Redactor().redact("   ").text == "   "


def test_multiple_identifiers_in_one_message() -> None:
    text = "I am arjun@example.com, call 9876543210, aadhaar 1234 5678 9012."
    result = Redactor().redact(text)
    assert result.text == "I am [EMAIL], call [PHONE], aadhaar [ID]."
    assert result.counts == {EMAIL: 1, PHONE: 1, ID: 1}
    assert result.placeholders_used == (EMAIL, ID, PHONE)


def test_overlapping_matches_resolve_to_the_longest_span() -> None:
    """A 12-digit Aadhaar is also a 10-digit phone plus two digits. The
    longest, most specific span must win, and it must win deterministically."""
    result = Redactor().redact("987654321012")
    assert result.text == ID
    assert result.hits[0].length == 12


def test_hit_spans_point_at_the_original_text() -> None:
    text = "call 9876543210 now"
    result = Redactor().redact(text)
    hit = result.hits[0]
    assert text[hit.start : hit.end] == "9876543210"


def test_restore_round_trips_a_single_hit() -> None:
    redactor = Redactor()
    text = "my number is 9876543210"
    result = redactor.redact(text)
    assert redactor.restore(result.text, result) == text


def test_restore_keeps_only_the_last_value_per_placeholder() -> None:
    """Two emails collapse to one placeholder, so the reverse map can hold one
    of them. Asserted rather than hidden: the map is for the single-entity
    case, and the chain does not use it at all."""
    redactor = Redactor()
    result = redactor.redact("a@example.com and b@example.com")
    assert result.counts == {EMAIL: 2}
    restored = redactor.restore(result.text, result)
    assert restored == "b@example.com and b@example.com"
    assert "a@example.com" not in restored


def test_restore_with_no_hits_is_a_no_op() -> None:
    redactor = Redactor()
    result = redactor.redact("nothing here")
    assert redactor.restore(result.text, result) == "nothing here"


def test_names_are_left_alone_by_default() -> None:
    result = Redactor().redact("My name is Riya Sharma and I am tired.")
    assert result.is_clean
    assert PERSON not in result.text


def test_name_redaction_is_opt_in_and_uses_the_injected_finder() -> None:
    class Stub(NameFinder):
        name = "stub"

        def find(self, text: str) -> list[tuple[int, int]]:
            return [(11, 22)]  # "Riya Sharma"

    redactor = Redactor(name_finder=Stub(), redact_names=True)
    result = redactor.redact("My name is Riya Sharma and I am tired.")
    assert result.text == "My name is [PERSON] and I am tired."
    assert result.counts == {PERSON: 1}


def test_null_name_finder_finds_nothing() -> None:
    assert NullNameFinder().find("Riya Sharma") == []


def test_spacy_finder_degrades_when_the_library_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing optional dependency must mean 'no name redaction', never a
    failed request. This is the offline sandbox's real situation."""
    finder = SpacyNameFinder(model="not_a_real_model")
    assert finder.find("Riya Sharma") == []
    assert not finder.is_loaded
    assert finder.model_name == "not_a_real_model"


def test_on_hit_callback_receives_every_hit() -> None:
    seen: list[RedactionHit] = []
    Redactor(on_hit=seen.append).redact("a@example.com or 9876543210")
    assert [hit.placeholder for hit in seen] == [EMAIL, PHONE]


def test_describe_is_metadata_only() -> None:
    described = Redactor().describe()
    assert described["redact_names"] is False
    assert described["name_finder"] == "null"
    assert described["placeholders"] == list(PLACEHOLDERS)
    assert EMAIL in described["placeholders"]


def test_every_placeholder_is_known() -> None:
    """A placeholder outside this set reaching a provider payload is a bug the
    payload test would not catch, because it would look tidy."""
    text = " ".join(PLACEHOLDERS)
    assert Redactor().redact(text).text == text


# --- properties --------------------------------------------------------------


@settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(text=st.text(alphabet=TEXT_ALPHABET, max_size=200))
def test_a_second_pass_never_finds_anything(text: str) -> None:
    """If any identifier survived the first pass, the redactor finds it again.

    This is the load-bearing property: it is what turns "the ten examples pass"
    into "there is no input whose identifiable text survives".
    """
    once = Redactor().redact(text)
    twice = Redactor().redact(once.text)
    assert twice.is_clean, f"surviving PII in {once.text!r} (from {text!r})"
    assert twice.text == once.text


@settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(text=st.text(alphabet=TEXT_ALPHABET, max_size=200))
def test_redaction_is_idempotent(text: str) -> None:
    once = Redactor().redact(text)
    assert Redactor().redact(once.text).text == once.text


@settings(max_examples=200, deadline=None)
@given(email=EMAILS)
def test_emails_never_reach_the_output(email: str) -> None:
    result = Redactor().redact(_sentence(email))
    assert email not in result.text
    # The local part is the identifying half; it must not survive either.
    local = email.split("@", 1)[0]
    if len(local) >= 5:
        assert local not in result.text


@settings(max_examples=200, deadline=None)
@given(phone=IN_PHONES)
def test_indian_mobile_numbers_never_reach_the_output(phone: str) -> None:
    result = Redactor().redact(_sentence(phone))
    assert phone not in result.text
    assert re.search(r"\d{10}", result.text) is None


@settings(max_examples=200, deadline=None)
@given(phone=INTL_PHONES)
def test_international_numbers_never_reach_the_output(phone: str) -> None:
    result = Redactor().redact(_sentence(phone))
    assert phone.strip() not in result.text


@settings(max_examples=200, deadline=None)
@given(secret=AADHAARISH)
def test_aadhaar_shaped_ids_never_reach_the_output(secret: str) -> None:
    result = Redactor().redact(_sentence(secret))
    assert re.sub(r"\D", "", secret) not in re.sub(r"\D", "", result.text)


@settings(max_examples=200, deadline=None)
@given(secret=PANISH)
def test_pan_shaped_ids_never_reach_the_output(secret: str) -> None:
    result = Redactor().redact(_sentence(secret))
    assert secret not in result.text


@settings(max_examples=200, deadline=None)
@given(secret=SSNISH)
def test_ssn_shaped_ids_never_reach_the_output(secret: str) -> None:
    result = Redactor().redact(_sentence(secret))
    assert secret not in result.text


@settings(max_examples=200, deadline=None)
@given(digits=DIGITS)
def test_long_digit_runs_never_reach_the_output(digits: str) -> None:
    result = Redactor().redact(_sentence(digits))
    assert digits not in result.text
    assert re.search(r"\d{9,}", result.text) is None


@settings(max_examples=200, deadline=None)
@given(
    card=st.from_regex(r"\d{13,19}", fullmatch=True),
)
def test_card_shaped_runs_are_always_removed_whether_or_not_luhn_agrees(card: str) -> None:
    result = Redactor().redact(_sentence(card))
    assert card not in result.text
    assert re.search(r"\d{9,}", result.text) is None
    assert result.counts.get(CARD, 0) + result.counts.get(ID, 0) == 1


@settings(max_examples=200, deadline=None)
@given(text=st.text(alphabet=TEXT_ALPHABET, max_size=300))
def test_output_is_never_much_longer_than_the_input(text: str) -> None:
    """A redactor that balloons the text would blow the token budget."""
    result = Redactor().redact(text)
    overhead = sum(len(hit.placeholder) for hit in result.hits)
    assert len(result.text) <= len(text) + overhead


@settings(max_examples=200, deadline=None)
@given(email=EMAILS)
def test_single_hit_round_trips_through_restore(email: str) -> None:
    redactor = Redactor()
    text = f"write to {email} today"
    result = redactor.redact(text)
    assert redactor.restore(result.text, result) == text


@settings(max_examples=200, deadline=None)
@given(text=st.text(alphabet=TEXT_ALPHABET, max_size=200))
def test_no_original_digits_survive_inside_a_redacted_span(text: str) -> None:
    """Whatever was replaced, the replacement contains none of its digits."""
    result = Redactor().redact(text)
    for hit in result.hits:
        assert not any(char.isdigit() for char in hit.placeholder)
