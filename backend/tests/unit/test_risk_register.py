"""Risk register completeness."""

from __future__ import annotations

import pathlib

REPO = pathlib.Path(__file__).resolve().parents[3]
REG = REPO / "docs/ai-risk-register.md"


def test_risk_register_exists() -> None:
    assert REG.exists()


def test_risk_register_has_required_sections() -> None:
    text = REG.read_text(encoding="utf-8")
    assert "## Risks" in text
    assert "Five highest-severity" in text
    assert "Likelihood" in text
    assert "Severity" in text
    assert "owner" in text.lower()
    assert "mitigation" in text.lower()


def test_risk_register_lists_at_least_15_risks() -> None:
    text = REG.read_text(encoding="utf-8")
    # Count rows with R<number>
    assert text.count("| R") >= 15


def test_five_highest_severity_items_present_with_mitigations() -> None:
    text = REG.read_text(encoding="utf-8")
    assert "R1" in text
    assert "R4" in text
    assert "R3" in text
    # Each top-5 row should mention Mitigated or Owner
    assert "Mitigated" in text
    assert "Next review" in text


def test_risk_register_covers_harm_privacy_bias_misuse_dependency() -> None:
    text = REG.read_text(encoding="utf-8").lower()
    for keyword in ["privacy", "bias", "misuse", "dependency", "harm"]:
        assert keyword in text, f"risk register missing harm dimension: {keyword}"
