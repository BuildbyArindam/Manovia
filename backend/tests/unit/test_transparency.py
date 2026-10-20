"""Transparency page: every AI component listed."""

from __future__ import annotations

import json
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[3]
FRONTEND_PAGE = REPO / "frontend/src/pages/TransparencyPage.tsx"
LOCALES = ["en", "hi", "bn"]


def test_transparency_page_exists() -> None:
    assert FRONTEND_PAGE.exists(), "TransparencyPage.tsx must exist"


def test_transparency_lists_every_ai_component() -> None:
    text = FRONTEND_PAGE.read_text(encoding="utf-8")
    required_ids = [
        "safety-rules",
        "safety-ml",
        "emotion",
        "llm",
        "output-guard",
        "retrieval",
        "voice",
        "agent",
        "forecasting",
        "bandit",
        "private-mode",
        "xr",
        "meditation",
        "biofeedback",
    ]
    for cid in required_ids:
        assert cid in text, f"Transparency page missing component {cid}"


def test_transparency_has_what_data_never_limits_and_off() -> None:
    text = FRONTEND_PAGE.read_text(encoding="utf-8")
    # Each card must mention data/never/limits/off concepts via i18n keys
    assert "dataHeading" in text or "Data" in text
    assert "neverHeading" in text or "Never" in text
    assert "limitsHeading" in text or "Limits" in text
    assert "howToTurnOff" in text or "turn it off" in text.lower()


def test_transparency_localised_en_hi_bn_have_same_keys() -> None:
    # Parity: each locale must have transparency section with same keys
    for lang in LOCALES:
        path = REPO / f"frontend/src/lib/i18n/locales/{lang}.json"
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert "transparency" in data, f"{lang} missing transparency"
        assert "title" in data["transparency"]
        assert "c1Name" in data["transparency"]
        assert "c14Name" in data["transparency"]
    # Check that placeholders match across locales
    en_trans = json.loads((REPO / "frontend/src/lib/i18n/locales/en.json").read_text(encoding="utf-8"))["transparency"]  # noqa: E501
    for lang in ["hi", "bn"]:
        other = json.loads((REPO / f"frontend/src/lib/i18n/locales/{lang}.json").read_text(encoding="utf-8"))["transparency"]  # noqa: E501
        assert set(en_trans.keys()) == set(other.keys()), f"parity mismatch {lang}: {set(en_trans.keys()) - set(other.keys())}"  # noqa: E501


def test_transparency_route_registered() -> None:
    app_tsx = (REPO / "frontend/src/App.tsx").read_text(encoding="utf-8")
    assert "/transparency" in app_tsx
    assert "TransparencyPage" in app_tsx
