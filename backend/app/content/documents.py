"""Load and validate ``consent_documents.json``."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files

from app.models.enums import ConsentKind

_DOCUMENTS_FILE = "consent_documents.json"


@dataclass(frozen=True)
class ConsentDocument:
    """One agreement at one version, as shown to users."""

    kind: ConsentKind
    version: str
    title: str
    summary: str
    text: str | None


class ConsentDocuments:
    """The full set of current consent documents, indexed by kind."""

    def __init__(self, documents: dict[ConsentKind, ConsentDocument]) -> None:
        self._documents = documents

    def for_kind(self, kind: ConsentKind) -> ConsentDocument:
        return self._documents[kind]

    def version_for(self, kind: ConsentKind) -> str:
        return self._documents[kind].version

    def all(self) -> list[ConsentDocument]:
        """Every document, in the enum's declaration order."""
        return [self._documents[kind] for kind in ConsentKind]

    def is_current(self, kind: ConsentKind, version: str) -> bool:
        """True when ``version`` is exactly the current version for ``kind``."""
        return version == self._documents[kind].version


@lru_cache(maxsize=1)
def load_consent_documents() -> ConsentDocuments:
    """Parse the shipped document set (cached; the file never changes at runtime)."""
    raw = files("app.content").joinpath(_DOCUMENTS_FILE).read_text(encoding="utf-8")
    payload = json.loads(raw)
    documents: dict[ConsentKind, ConsentDocument] = {}
    for kind in ConsentKind:
        entry = payload["documents"].get(kind.value)
        if entry is None:
            raise ValueError(f"consent_documents.json is missing {kind.value!r}")
        version = str(entry["version"])
        if not version or len(version) > 32:
            raise ValueError(f"consent version for {kind.value!r} must fit 1..32 chars")
        documents[kind] = ConsentDocument(
            kind=kind,
            version=version,
            title=str(entry["title"]),
            summary=str(entry["summary"]),
            text=str(entry["text"]) if entry.get("text") is not None else None,
        )
    return ConsentDocuments(documents)
