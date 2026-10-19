"""The printable PDF: one page-set a professional can hold, with the banner on it.

Rendered with ReportLab's Platypus flowables, which means real pagination, a
footer on every page and no HTML-to-PDF browser in the dependency tree. Two rules
shape the output:

1. **The banner is on every page, not just the first.** A printed page that lands
   on a desk without its header must still say "patient-generated, not a
   diagnosis", so the footer carries it too and the first page carries it twice —
   once as the title block and once as a boxed statement above any data.
2. **The font is honest about its limits.** The built-in Helvetica encodes
   WinAnsi/cp1252, so Devanagari and Bengali text cannot be drawn with it. Rather
   than emit black boxes, characters outside that encoding are replaced and the
   document says so on page one, pointing at the on-screen version (which has no
   such limit) for the full text. Embedding a Unicode font is in the parking lot.

Nothing in this module reads the database: it renders a
:class:`~app.services.sharing.items.SharePayload`, the same object the preview,
the FHIR bundle and the ciphertext are made from.
"""

from __future__ import annotations

import io
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    Flowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.models.base import as_utc
from app.services.sharing.items import (
    DISCLAIMER,
    DISCLAIMER_TITLE,
    NEVER_INCLUDED,
    SharePayload,
)

#: PDF metadata + page furniture.
PDF_TITLE = f"{DISCLAIMER_TITLE}: appointment summary from Manovia"
PDF_AUTHOR = "Manovia (patient-generated)"
PDF_SUBJECT = DISCLAIMER_TITLE
PDF_CREATOR = "Manovia"

#: The marker that replaces a character Helvetica cannot draw.
UNRENDERABLE = "?"

#: Page geometry (A4: the paper size most of the world's clinics print on).
PAGE_SIZE = A4
MARGIN = 18 * mm

_INK = colors.HexColor("#1f2933")
_MUTED = colors.HexColor("#52606d")
_LINE = colors.HexColor("#cbd2d9")
_BOX = colors.HexColor("#f5f7fa")
_ACCENT = colors.HexColor("#7a3b12")


def _styles() -> dict[str, ParagraphStyle]:
    """The paragraph styles, built fresh per render (styles are mutable)."""

    base = ParagraphStyle(
        "base", fontName="Helvetica", fontSize=9.5, leading=13, textColor=_INK, alignment=TA_LEFT
    )
    return {
        "base": base,
        "title": ParagraphStyle(
            "title", parent=base, fontName="Helvetica-Bold", fontSize=15, leading=19
        ),
        "h2": ParagraphStyle(
            "h2",
            parent=base,
            fontName="Helvetica-Bold",
            fontSize=11.5,
            leading=15,
            spaceBefore=8,
            spaceAfter=3,
        ),
        "h3": ParagraphStyle(
            "h3", parent=base, fontName="Helvetica-Bold", fontSize=10, leading=13, spaceBefore=4
        ),
        "muted": ParagraphStyle("muted", parent=base, fontSize=8.5, leading=11.5, textColor=_MUTED),
        "banner": ParagraphStyle(
            "banner", parent=base, fontName="Helvetica-Bold", fontSize=10.5, leading=14
        ),
        "cell": ParagraphStyle("cell", parent=base, fontSize=8.5, leading=11),
        "cell_head": ParagraphStyle(
            "cell_head", parent=base, fontName="Helvetica-Bold", fontSize=8.5, leading=11
        ),
    }


def pdf_safe(value: str) -> tuple[str, bool]:
    """Make text drawable in Helvetica, and report whether anything was replaced.

    Returns ``(text, replaced)``. ``replaced`` is what lets the document tell the
    reader that some characters are missing instead of silently showing boxes.
    """

    out: list[str] = []
    replaced = False
    for character in value:
        try:
            character.encode("cp1252")
        except UnicodeEncodeError:
            replaced = True
            out.append(UNRENDERABLE)
            continue
        out.append(character)
    return "".join(out), replaced


def escape(value: str) -> str:
    """Escape text for a Paragraph *and* make it drawable.

    ReportLab paragraphs are XML-ish, so ``&``/``<``/``>`` must be escaped before
    the text reaches a flowable — a thought record containing ``<3`` would
    otherwise break the document instead of printing it.
    """

    text, _ = pdf_safe(value)
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _stamp(value: datetime) -> str:
    """An ISO-ish UTC timestamp for a printed line."""

    return as_utc(value).astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


def _day(value: datetime) -> str:
    return as_utc(value).astimezone(UTC).date().isoformat()


def _table(rows: Sequence[Sequence[Any]], widths: Sequence[float], styles: dict[str, Any]) -> Table:
    """A bordered table whose cells are Paragraphs (so long text wraps)."""

    data = [
        [
            cell
            if isinstance(cell, Flowable)
            else Paragraph(escape(str(cell)), styles["cell_head"] if index == 0 else styles["cell"])
            for cell in row
        ]
        for index, row in enumerate(rows)
    ]
    table = Table(data, colWidths=list(widths), hAlign="LEFT", repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), _BOX),
                ("GRID", (0, 0), (-1, -1), 0.4, _LINE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    return table


def _banner_block(styles: dict[str, ParagraphStyle]) -> Table:
    """The boxed statement that opens the document."""

    inner = [
        [Paragraph(escape(DISCLAIMER_TITLE), styles["banner"])],
        [Paragraph(escape(DISCLAIMER), styles["cell"])],
    ]
    box = Table(inner, colWidths=[PAGE_SIZE[0] - 2 * MARGIN], hAlign="LEFT")
    box.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.8, _ACCENT),
                ("BACKGROUND", (0, 0), (-1, -1), _BOX),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    return box


def _footer(canvas: Any, document: Any) -> None:
    """The banner and the page number, on every page including the first."""

    canvas.saveState()
    width, _height = PAGE_SIZE
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(_MUTED)
    text, _ = pdf_safe(f"{DISCLAIMER_TITLE} — Manovia is not an EHR and not a crisis service.")
    canvas.drawString(MARGIN, 12 * mm, text)
    canvas.drawRightString(width - MARGIN, 12 * mm, f"Page {document.page}")
    canvas.setStrokeColor(_LINE)
    canvas.setLineWidth(0.4)
    canvas.line(MARGIN, 15 * mm, width - MARGIN, 15 * mm)
    canvas.restoreState()


def _overview_rows(payload: SharePayload) -> list[list[str]]:
    """The 'what is in this document' table, straight from the payload."""

    rows: list[list[str]] = [["Item", "What is here"]]
    if payload.identity:
        rows.append(["Identified as", payload.identity])
    if payload.mood_trend is not None:
        trend = payload.mood_trend
        rows.append(
            [
                "Mood trend summary",
                f"{trend.entries} check-ins over {trend.window_days} days; mean mood "
                f"{trend.mean_valence if trend.mean_valence is not None else 'n/a'}, mean energy "
                f"{trend.mean_energy if trend.mean_energy is not None else 'n/a'} (1-5)",
            ]
        )
    if payload.assessments is not None:
        rows.append(
            [
                "PHQ-9 / GAD-7 history",
                f"{len(payload.assessments)} screening totals with band labels "
                "(individual answers not included)",
            ]
        )
    if payload.thought_records is not None:
        rows.append(
            ["Selected thought records", f"{len(payload.thought_records)} record(s), in full"]
        )
    if payload.exercise_helpfulness is not None:
        rows.append(
            [
                "Exercise helpfulness",
                f"{payload.exercise_helpfulness.completions} completions across "
                f"{len(payload.exercise_helpfulness.exercises)} exercise(s)",
            ]
        )
    if payload.note is not None:
        rows.append(
            ["Note for the appointment", f"{len(payload.note)} characters, written by the person"]
        )
    if payload.questions is not None:
        rows.append(["Questions to ask", f"{len(payload.questions)} question(s)"])
    return rows


def _flowables(payload: SharePayload) -> tuple[list[Flowable], bool]:
    """Every flowable in reading order, and whether any text was substituted."""

    styles = _styles()
    replaced_any = False
    story: list[Flowable] = []

    def track(text: str) -> str:
        """Escape for a Paragraph and remember whether anything was replaced."""

        nonlocal replaced_any
        safe, replaced = pdf_safe(text)
        replaced_any = replaced_any or replaced
        return safe.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    story.append(Paragraph(track(PDF_TITLE), styles["title"]))
    story.append(
        Paragraph(
            track(
                f"Prepared {_stamp(payload.generated_at)} by the person the data belongs to, "
                f"in {payload.app} {payload.app_version}."
            ),
            styles["muted"],
        )
    )
    story.append(Spacer(1, 6))
    story.append(_banner_block(styles))
    story.append(Spacer(1, 10))

    story.append(Paragraph(track("What is in this document"), styles["h2"]))
    story.append(_table(_overview_rows(payload), [45 * mm, 129 * mm], styles))
    story.append(Spacer(1, 4))
    story.append(
        Paragraph(
            track("Never included: " + "; ".join(NEVER_INCLUDED) + "."),
            styles["muted"],
        )
    )
    # The note about substituted characters belongs here, but whether it is
    # needed is only known once every section has been rendered — a Devanagari
    # note appears near the end of the document. So the paragraph is built now
    # and inserted at this position at the end, rather than being decided too
    # early and silently dropped.
    substitution_slot = len(story)
    substitution_note = Paragraph(
        track(
            "Note on characters: this PDF uses a built-in Latin font. Characters it "
            f"cannot draw (for example Devanagari or Bengali) appear as "
            f"'{UNRENDERABLE}'. The on-screen version of this share shows the full text."
        ),
        styles["muted"],
    )

    if payload.mood_trend is not None:
        trend = payload.mood_trend
        story.append(Paragraph(track("Mood trend summary"), styles["h2"]))
        rows: list[list[str]] = [["Measure", "Value"]]
        rows.append(["Window", f"last {trend.window_days} days (from {_day(trend.window_start)})"])
        rows.append(["Check-ins", str(trend.entries)])
        rows.append(
            ["Mean mood (1-5)", "n/a" if trend.mean_valence is None else f"{trend.mean_valence}"]
        )
        rows.append(
            ["Mean energy (1-5)", "n/a" if trend.mean_energy is None else f"{trend.mean_energy}"]
        )
        halves = (
            "not enough check-ins for a direction"
            if trend.earlier_mean_valence is None or trend.recent_mean_valence is None
            else (
                f"{trend.earlier_mean_valence} then {trend.recent_mean_valence} ({trend.direction})"
            )
        )
        rows.append(["First half vs second half (mean mood)", halves])
        rows.append(["Direction", trend.direction_note])
        if trend.first_check_in is not None and trend.last_check_in is not None:
            rows.append(
                [
                    "First / last check-in",
                    f"{_day(trend.first_check_in)} to {_day(trend.last_check_in)}",
                ]
            )
        if trend.top_emotions:
            rows.append(
                [
                    "Most-used emotion tags",
                    ", ".join(f"{item.label} ({item.count})" for item in trend.top_emotions),
                ]
            )
        if trend.top_factors:
            rows.append(
                [
                    "Most-used factor tags",
                    ", ".join(f"{item.label} ({item.count})" for item in trend.top_factors),
                ]
            )
        rows.append(["Check-in notes", "not included"])
        story.append(_table(rows, [45 * mm, 129 * mm], styles))

    if payload.assessments:
        story.append(Paragraph(track("PHQ-9 and GAD-7 history"), styles["h2"]))
        story.append(
            Paragraph(
                track(
                    "Self-report screening totals. The band label is a self-help label for "
                    "reflection, not a diagnosis. LOINC terms: PHQ-9 total score 44261-6, "
                    "GAD-7 total score 70274-6 (verified at loinc.org)."
                ),
                styles["muted"],
            )
        )
        rows = [["Date", "Questionnaire", "Total", "Band", "LOINC"]]
        for item in payload.assessments:
            rows.append(
                [
                    _day(item.completed_at),
                    item.instrument_name,
                    str(item.total),
                    item.band_label,
                    item.loinc_code,
                ]
            )
        story.append(_table(rows, [26 * mm, 34 * mm, 18 * mm, 40 * mm, 56 * mm], styles))

    if payload.thought_records:
        story.append(Paragraph(track("Thought records"), styles["h2"]))
        for index, record in enumerate(payload.thought_records, start=1):
            story.append(
                Paragraph(
                    track(
                        f"{index}. {record.emotion} — {_day(record.created_at)} — intensity "
                        f"{record.intensity_before}/100 before, {record.intensity_after}/100 after"
                    ),
                    styles["h3"],
                )
            )
            if record.what_helped:
                story.append(
                    Paragraph(
                        track("What helped: " + ", ".join(record.what_helped)), styles["muted"]
                    )
                )
            for field, label in (
                ("situation", "Situation"),
                ("automatic_thought", "Automatic thought"),
                ("evidence_for", "Evidence for"),
                ("evidence_against", "Evidence against"),
                ("balanced_thought", "Balanced thought"),
            ):
                story.append(Paragraph(track(label), styles["h3"]))
                story.append(Paragraph(track(str(getattr(record, field))), styles["base"]))

    if payload.exercise_helpfulness is not None:
        summary = payload.exercise_helpfulness
        story.append(Paragraph(track("Exercise helpfulness"), styles["h2"]))
        rows = [["Exercise", "Times", "Mean before", "Mean after", "Mean change"]]
        for row in summary.exercises:
            rows.append(
                [
                    row.title,
                    str(row.times),
                    "n/a" if row.mean_before is None else f"{row.mean_before:.2f}",
                    "n/a" if row.mean_after is None else f"{row.mean_after:.2f}",
                    "n/a" if row.mean_change is None else f"{row.mean_change:+.2f}",
                ]
            )
        story.append(_table(rows, [70 * mm, 20 * mm, 28 * mm, 28 * mm, 28 * mm], styles))
        story.append(
            Paragraph(
                track(
                    f"{summary.completions} completions in the last {summary.window_days} days; "
                    f"{summary.rated_completions} had both ratings. Scale: {summary.rating_scale}. "
                    f"{summary.change_caveat}"
                ),
                styles["muted"],
            )
        )

    if payload.note is not None:
        story.append(Paragraph(track("Note for the appointment"), styles["h2"]))
        for line in payload.note.splitlines() or [payload.note]:
            story.append(Paragraph(track(line) or "&nbsp;", styles["base"]))

    if payload.questions:
        story.append(Paragraph(track("Questions to ask"), styles["h2"]))
        for index, question in enumerate(payload.questions, start=1):
            story.append(Paragraph(track(f"{index}. {question}"), styles["base"]))

    story.append(Spacer(1, 12))
    story.append(
        Paragraph(
            track(
                "Manovia is a self-help companion, not a clinical service, not an electronic "
                "health record and not a crisis service. This document was generated for one "
                "appointment, shared by the person it belongs to, and should be read as "
                "patient-generated information."
            ),
            styles["muted"],
        )
    )
    if replaced_any:
        story.insert(substitution_slot, substitution_note)
    return story, replaced_any


def render_pdf(payload: SharePayload) -> bytes:
    """Render the printable summary. Returns the whole PDF as bytes."""

    story, _replaced = _flowables(payload)
    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=PAGE_SIZE,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=MARGIN,
        bottomMargin=22 * mm,
        title=PDF_TITLE,
        author=PDF_AUTHOR,
        subject=PDF_SUBJECT,
        creator=PDF_CREATOR,
    )
    document.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buffer.getvalue()


__all__ = [
    "DISCLAIMER",
    "DISCLAIMER_TITLE",
    "MARGIN",
    "PAGE_SIZE",
    "PDF_AUTHOR",
    "PDF_CREATOR",
    "PDF_SUBJECT",
    "PDF_TITLE",
    "UNRENDERABLE",
    "escape",
    "pdf_safe",
    "render_pdf",
]
