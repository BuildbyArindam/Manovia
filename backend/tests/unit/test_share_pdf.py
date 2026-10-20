"""Unit tests for the printable PDF.

The PDF is the artefact most likely to be printed, folded and handed across a
desk, so the things tested here are the things a reader would notice:

* the "patient-generated, not a diagnosis" banner is boxed on page one **and** in
  the footer of every page, because a loose page must still say what it is;
* the document lists what is in it and what is never in it, in the same words the
  consent screen used;
* the screening totals carry their LOINC codes and the statement that individual
  answers are not included;
* the note and the questions are verbatim — a paraphrase of somebody's words in a
  clinical document would be a small forgery;
* the built-in Latin font cannot draw Devanagari or Bengali, so those characters
  are replaced *and the replacement is announced on the page*. Silently printing
  a black box, or silently dropping the text, are both worse.

Text is compared after collapsing whitespace, because extraction from a PDF
inserts line breaks where the layout did.
"""

from __future__ import annotations

import io
import re

import pytest
from pypdf import PdfReader

from app.models.enums import ShareItem
from app.services.sharing.items import DISCLAIMER, DISCLAIMER_TITLE, NEVER_INCLUDED
from app.services.sharing.pdf import (
    MARGIN,
    PAGE_SIZE,
    PDF_AUTHOR,
    PDF_CREATOR,
    PDF_SUBJECT,
    PDF_TITLE,
    UNRENDERABLE,
    escape,
    pdf_safe,
    render_pdf,
)
from tests.sharing_helpers import NOTE, THOUGHT, sample_payload

FOOTER = "Manovia is not an EHR and not a crisis service"


def pages(payload: object) -> list[str]:
    """The extracted text of every page of a rendered summary."""

    raw = render_pdf(payload)  # type: ignore[arg-type]
    reader = PdfReader(io.BytesIO(raw))
    return [(page.extract_text() or "") for page in reader.pages]


def flat(payload: object) -> str:
    """The whole document as one whitespace-normalised string."""

    return normalise("\n".join(pages(payload)))


def normalise(text: str) -> str:
    """Collapse the line breaks a PDF layout introduces."""

    return re.sub(r"\s+", " ", text).strip()


def render(payload: object) -> bytes:
    return render_pdf(payload)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# the document itself
# --------------------------------------------------------------------------- #


def test_it_is_a_pdf_of_a_readable_size() -> None:
    """Real PDF bytes, and not a stub."""

    raw = render(sample_payload())
    assert raw.startswith(b"%PDF-")
    assert len(raw) > 3000
    reader = PdfReader(io.BytesIO(raw))
    assert len(reader.pages) >= 1
    box = reader.pages[0].mediabox
    assert float(box.width) == pytest.approx(PAGE_SIZE[0], abs=1)
    assert float(box.height) == pytest.approx(PAGE_SIZE[1], abs=1)


def test_the_metadata_says_what_this_document_is() -> None:
    """A file that leaves the app should describe itself in a file manager too."""

    reader = PdfReader(io.BytesIO(render(sample_payload())))
    metadata = reader.metadata
    assert metadata is not None
    assert metadata.title == PDF_TITLE
    assert metadata.author == PDF_AUTHOR
    assert metadata.subject == PDF_SUBJECT == DISCLAIMER_TITLE
    assert metadata.creator == PDF_CREATOR
    assert DISCLAIMER_TITLE in str(metadata.title)


def test_the_banner_is_on_page_one_above_any_data() -> None:
    """Boxed, complete, and before the first number in the document."""

    text = pages(sample_payload())[0]
    assert DISCLAIMER_TITLE in text
    for sentence in ("not a diagnosis", "not a clinical assessment", "not a medical record"):
        assert sentence in normalise(text)
    assert normalise(DISCLAIMER) in normalise(text)
    # The banner comes before the mood numbers.
    assert text.index("Patient-generated") < text.index("Mean mood")


def test_the_banner_is_in_the_footer_of_every_page() -> None:
    """A single printed page, separated from the rest, still says what it is."""

    payload = sample_payload(note="\n".join(f"line {index}" for index in range(120)))
    found = pages(payload)
    assert len(found) > 1, "the test needs a document that runs past one page"
    for index, page in enumerate(found, start=1):
        assert DISCLAIMER_TITLE in normalise(page), index
        assert FOOTER in normalise(page), index
        assert f"Page {index}" in normalise(page), index


def test_the_overview_table_lists_what_is_in_and_out_of_the_document() -> None:
    """The printed page carries the same comprehension sentence as the screen."""

    text = flat(sample_payload())
    assert "What is in this document" in text
    for row in (
        "Identified as",
        "Mood trend summary",
        "PHQ-9 / GAD-7 history",
        "Selected thought records",
        "Exercise helpfulness",
        "Note for the appointment",
        "Questions to ask",
    ):
        assert row in text
    assert "Never included:" in text
    for excluded in NEVER_INCLUDED:
        assert normalise(excluded) in text


def test_screening_rows_carry_the_loinc_codes_and_the_no_answers_statement() -> None:
    """A professional can trace each total to its term."""

    text = flat(sample_payload())
    assert "44261-6" in text
    assert "70274-6" in text
    assert "individual answers not included" in text
    assert "Moderate" in text and "Mild" in text
    assert "12" in text and "9" in text


def test_the_mood_section_reports_numbers_and_never_a_note() -> None:
    """Counts and averages, with the direction rule stated in words."""

    text = flat(sample_payload())
    assert "6 check-ins over 30 days" in text
    assert "mean mood 3.0" in text
    assert "mean energy 3.5" in text
    assert "second half of this window were higher" in text
    assert "Most-used emotion tags" in text


def test_the_thought_record_is_printed_in_full_with_its_five_sections() -> None:
    """What the person wrote, in the order they wrote it."""

    text = flat(sample_payload())
    for heading in (
        "Situation",
        "Automatic thought",
        "Evidence for",
        "Evidence against",
        "Balanced thought",
    ):
        assert heading in text
    for value in THOUGHT.values():
        assert normalise(value) in text
    assert "80/100" in text and "45/100" in text
    assert "looking at evidence" in text


def test_the_note_and_questions_are_verbatim() -> None:
    """No paraphrase, no truncation, and the questions are numbered."""

    text = flat(sample_payload())
    assert normalise(NOTE) in text
    assert "1. Is this normal?" in text
    assert "2. What should I try next?" in text


def test_the_exercise_section_states_the_scale_and_the_caveat() -> None:
    """A moved rating is an association, and the paper says so."""

    text = flat(sample_payload())
    assert "Box breathing" in text
    assert "1 (very low) to 5 (very good)" in text
    assert "association, not proof" in text
    assert "+1.67" in text


def test_the_closing_paragraph_says_what_manovia_is_not() -> None:
    """The last thing on the last page."""

    text = flat(sample_payload())
    assert "not a clinical service" in text
    assert "not an electronic health record" in text
    assert "not a crisis service" in text
    assert "patient-generated information" in text


def test_a_minimal_selection_still_renders_a_complete_document() -> None:
    """One item, no identity: a page that is honest about being almost empty."""

    payload = sample_payload(
        items=[ShareItem.NOTE],
        identity=None,
        mood_trend=None,
        assessments=None,
        thought_records=None,
        exercise_helpfulness=None,
        questions=None,
    )
    text = flat(payload)
    assert DISCLAIMER_TITLE in text
    assert normalise(NOTE) in text
    assert "44261-6" not in text
    assert "Identified as" not in text
    assert len(pages(payload)) == 1


def test_an_anonymous_account_does_not_get_an_email_on_the_page() -> None:
    """The identity line appears only when the person ticked it."""

    assert "someone@example.test" in flat(sample_payload())
    assert "someone@example.test" not in flat(sample_payload(identity=None))


def test_the_same_payload_renders_the_same_document() -> None:
    """Byte equality is not available (the PDF carries a timestamp), text is."""

    payload = sample_payload()
    assert flat(payload) == flat(payload)
    assert len(pages(payload)) == len(pages(payload))


def test_xml_characters_in_somebody_s_words_do_not_break_the_document() -> None:
    """``<3`` and an ampersand are printed, not parsed."""

    payload = sample_payload(
        note="I felt <3 and & anxious > everything",
        questions=["Is 5 < 6 & 7 > 8?"],
    )
    text = flat(payload)
    assert "I felt <3 and & anxious > everything" in text
    assert "Is 5 < 6 & 7 > 8?" in text


# --------------------------------------------------------------------------- #
# the font's limits, stated on the page
# --------------------------------------------------------------------------- #


def test_pdf_safe_replaces_only_what_helvetica_cannot_draw() -> None:
    """Latin text passes through untouched; other scripts are marked."""

    assert pdf_safe("Plain ASCII, émojis aside") == ("Plain ASCII, émojis aside", False)
    for script in ("हिन्दी", "বাংলা", "日本語", "🙂"):
        safe, replaced = pdf_safe(script)
        assert safe == UNRENDERABLE * len(script), script
        assert replaced is True, script
    text, replaced = pdf_safe("Mixed हिन्दी text")
    assert text == f"Mixed {UNRENDERABLE * len('हिन्दी')} text"
    assert replaced is True
    assert pdf_safe("") == ("", False)
    # An arrow or a bullet is outside cp1252 too, and must not be hard-coded.
    assert pdf_safe("a \u2192 b")[1] is True


def test_escape_handles_both_problems_at_once() -> None:
    """XML metacharacters for the paragraph, and the font for the glyph."""

    assert escape("a & b") == "a &amp; b"
    assert escape("<3") == "&lt;3"
    assert escape("a > b") == "a &gt; b"
    assert escape("हिन्दी") == UNRENDERABLE * len("हिन्दी")
    assert escape("&<>हि") == f"&amp;&lt;&gt;{UNRENDERABLE * len('हि')}"


def test_a_substitution_is_announced_on_the_page_and_only_then() -> None:
    """The reader is told characters are missing, and where to read them."""

    plain = flat(sample_payload())
    assert "Note on characters" not in plain

    marked = flat(sample_payload(note="मेरी नींद खराब है"))
    assert "Note on characters" in marked
    assert "built-in Latin font" in marked
    assert "on-screen version" in marked
    assert UNRENDERABLE in marked
    # The rest of the note still renders around the substituted characters.
    assert "Note for the appointment" in marked


def test_a_note_in_another_script_does_not_lose_its_place_in_the_document() -> None:
    """Substitution changes glyphs, not structure."""

    payload = sample_payload(note="বাংলা note", questions=["প্রশ্ন?"])
    found = pages(payload)
    assert found
    text = normalise("\n".join(found))
    assert "Note for the appointment" in text
    assert "Questions to ask" in text
    assert f"{UNRENDERABLE} note" in text


def test_the_page_is_a4_with_margins_a_printer_can_honour() -> None:
    """18mm in points, on the paper size most clinics print on."""

    assert pytest.approx(18 * 2.8346, abs=1) == MARGIN
    assert PAGE_SIZE[0] == pytest.approx(595.28, abs=0.5)
    assert PAGE_SIZE[1] == pytest.approx(841.89, abs=0.5)
