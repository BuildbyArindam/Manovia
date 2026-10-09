"""The shipped consent documents, including the AI disclosure (Day 4)."""

from __future__ import annotations

import pytest

from app.content import ConsentDocuments, load_consent_documents
from app.models.enums import ConsentKind


def test_every_consent_kind_has_a_document() -> None:
    documents = load_consent_documents()
    assert [document.kind for document in documents.all()] == list(ConsentKind)


def test_versions_fit_the_database_column() -> None:
    for document in load_consent_documents().all():
        assert document.version, f"{document.kind} has no version"
        assert len(document.version) <= 32
        assert document.title
        assert document.summary


def test_ai_disclosure_text_discloses_the_ai_nature() -> None:
    """AGENTS.md rule 3: never claim to be human; disclose the AI nature."""
    disclosure = load_consent_documents().for_kind(ConsentKind.AI_DISCLOSURE)
    assert disclosure.text is not None
    lowered = disclosure.text.lower()
    assert "ai" in lowered
    assert "not written by a human" in lowered
    assert "therapist" in lowered or "counsellor" in lowered
    assert "not an emergency or crisis service" in lowered
    assert "delete your account" in lowered


def test_version_lookup_and_currency_checks() -> None:
    documents = load_consent_documents()
    version = documents.version_for(ConsentKind.TERMS)
    assert documents.is_current(ConsentKind.TERMS, version)
    assert not documents.is_current(ConsentKind.TERMS, "1999-01-01")
    for kind in ConsentKind:
        assert documents.version_for(kind) == documents.for_kind(kind).version


def test_loader_is_cached_and_validated() -> None:
    assert load_consent_documents() is load_consent_documents()
    assert isinstance(load_consent_documents(), ConsentDocuments)


def test_loader_rejects_a_missing_document(monkeypatch: pytest.MonkeyPatch) -> None:
    """A document set missing a kind is a startup error, not a silent gap."""
    import json
    from importlib.resources import files as resource_files

    from app.content import documents as documents_module

    payload = json.loads(
        resource_files("app.content").joinpath(documents_module._DOCUMENTS_FILE).read_text("utf-8")
    )
    del payload["documents"]["privacy"]

    class FakeTraversable:
        def __init__(self, text: str) -> None:
            self._text = text

        def joinpath(self, name: str) -> FakeTraversable:
            return self

        def read_text(self, encoding: str = "utf-8") -> str:
            return self._text

    monkeypatch.setattr(
        "app.content.documents.files",
        lambda package: FakeTraversable(json.dumps(payload)),
    )
    documents_module.load_consent_documents.cache_clear()
    try:
        with pytest.raises(ValueError, match="privacy"):
            documents_module.load_consent_documents()
    finally:
        documents_module.load_consent_documents.cache_clear()


def test_loader_rejects_an_oversized_version(monkeypatch: pytest.MonkeyPatch) -> None:
    """A version longer than the consents.version column must fail at startup."""
    import json
    from importlib.resources import files as resource_files

    from app.content import documents as documents_module

    payload = json.loads(
        resource_files("app.content").joinpath(documents_module._DOCUMENTS_FILE).read_text("utf-8")
    )
    payload["documents"]["terms"]["version"] = "x" * 33

    class FakeTraversable:
        def __init__(self, text: str) -> None:
            self._text = text

        def joinpath(self, name: str) -> FakeTraversable:
            return self

        def read_text(self, encoding: str = "utf-8") -> str:
            return self._text

    monkeypatch.setattr(
        "app.content.documents.files",
        lambda package: FakeTraversable(json.dumps(payload)),
    )
    documents_module.load_consent_documents.cache_clear()
    try:
        with pytest.raises(ValueError, match=r"1\.\.32"):
            documents_module.load_consent_documents()
    finally:
        documents_module.load_consent_documents.cache_clear()
