"""The curated knowledge base: loading and validating the markdown documents.

The documents live in ``app/content/knowledge/`` — one file per topic, with
YAML front-matter carrying the metadata retrieval and the UI need::

    ---
    id: grounding-five-senses
    title: Grounding with your five senses
    tags: [grounding, calm, panic]
    language: en
    evidence_note: reflects grounding exercises widely taught in CBT-informed self-help
    reviewed: false
    ---

``id`` must equal the filename stem, so a document can always be found again
from the id shown in chat metadata, and a renamed file fails loudly instead of
silently orphaning its references.

This module deliberately knows nothing about vectors or embeddings: it is the
authoring-facing half of the knowledge base (see
``docs/knowledge-authoring-guide.md``). Chunking and retrieval live in
:mod:`app.services.rag`.

Loading re-reads the directory on every call. The corpus is ~40 small files,
the surface is low-traffic, and a cache here would be keyed on nothing that
can change — the honest trade is a fresh read so an edited document is live on
the next request, with no invalidation question to answer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from importlib.resources import files
from typing import Any

import yaml

from app.services.safety.injection import looks_like_injection

#: The package directory that holds the markdown corpus.
KNOWLEDGE_PACKAGE_DIR = "knowledge"

#: Languages the corpus (and the retriever's metadata filter) supports. The
#: chat surface speaks en/hi/bn; a document in a new language needs a locale
#: file and review before it can be served.
KNOWLEDGE_LANGUAGES: tuple[str, ...] = ("en", "hi", "bn")

#: A document id is also its filename stem, so it must be a safe slug.
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_TAG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

#: Body length bounds in characters. Roughly: short enough to read in a
#: minute, long enough to say something useful.
MIN_BODY_CHARS = 200
MAX_BODY_CHARS = 4000

_REQUIRED_KEYS = ("id", "title", "tags", "language", "evidence_note", "reviewed")


class KnowledgeContentError(RuntimeError):
    """A knowledge document is missing, malformed, or breaks a corpus rule."""


@dataclass(frozen=True)
class KnowledgeDoc:
    """One curated self-help document, metadata split from the body."""

    id: str
    title: str
    tags: tuple[str, ...]
    language: str
    #: What general approach the piece reflects ("based on widely taught CBT
    #: techniques"). Never a claim of clinical validation.
    evidence_note: str
    #: True only after the human safety review described in
    #: ``docs/knowledge-authoring-guide.md``. New documents ship as False.
    reviewed: bool
    body: str


def knowledge_dir() -> Any:
    """The packaged corpus directory (an importlib Traversable)."""
    return files("app.content").joinpath(KNOWLEDGE_PACKAGE_DIR)


def parse_knowledge_doc(path_name: str, raw: str) -> KnowledgeDoc:
    """Parse and validate one markdown document. Raises on any rule break."""
    if not raw.startswith("---"):
        raise KnowledgeContentError(f"{path_name}: document must start with YAML front-matter")
    end = raw.find("\n---", 3)
    if end == -1:
        raise KnowledgeContentError(f"{path_name}: front-matter is never closed")
    header, body = raw[3:end], raw[end + 4 :]
    try:
        meta: Any = yaml.safe_load(header)
    except yaml.YAMLError as exc:
        raise KnowledgeContentError(f"{path_name}: front-matter is not valid YAML: {exc}") from exc
    if not isinstance(meta, dict):
        raise KnowledgeContentError(f"{path_name}: front-matter must be a mapping")

    missing = [key for key in _REQUIRED_KEYS if key not in meta]
    if missing:
        raise KnowledgeContentError(f"{path_name}: front-matter missing {', '.join(missing)}")

    doc_id = meta["id"]
    if not isinstance(doc_id, str) or not _ID_RE.match(doc_id):
        raise KnowledgeContentError(
            f"{path_name}: id must be a lowercase slug of letters, digits and dashes"
        )
    if doc_id != path_name.removesuffix(".md"):
        raise KnowledgeContentError(f"{path_name}: id {doc_id!r} does not match the filename stem")

    title = meta["title"]
    if not isinstance(title, str) or not 3 <= len(title.strip()) <= 90:
        raise KnowledgeContentError(f"{path_name}: title must be 3-90 characters")
    title = title.strip()

    tags = meta["tags"]
    if not isinstance(tags, list) or not tags:
        raise KnowledgeContentError(f"{path_name}: tags must be a non-empty YAML list")
    cleaned: list[str] = []
    for tag in tags:
        if not isinstance(tag, str) or not _TAG_RE.match(tag):
            raise KnowledgeContentError(
                f"{path_name}: tag {tag!r} must be a lowercase slug of letters, digits and dashes"
            )
        if tag not in cleaned:
            cleaned.append(tag)

    language = meta["language"]
    if language not in KNOWLEDGE_LANGUAGES:
        raise KnowledgeContentError(
            f"{path_name}: language must be one of {', '.join(KNOWLEDGE_LANGUAGES)} "
            f"(got {language!r})"
        )

    evidence_note = meta["evidence_note"]
    if not isinstance(evidence_note, str) or not 3 <= len(evidence_note.strip()) <= 250:
        raise KnowledgeContentError(f"{path_name}: evidence_note must be 3-250 characters")

    reviewed = meta["reviewed"]
    if not isinstance(reviewed, bool):
        raise KnowledgeContentError(f"{path_name}: reviewed must be true or false")

    body = body.strip()
    # Day 29 (RT-I-KB-01): reference material is read by the model, so a
    # document that addresses the model is an authoring error, not content.
    if looks_like_injection(f"{title}\n{body}"):
        raise KnowledgeContentError(
            f"{path_name}: contains instruction-shaped text aimed at the model "
            "(e.g. 'ignore previous instructions'); rewrite it as guidance for the reader"
        )
    if not MIN_BODY_CHARS <= len(body) <= MAX_BODY_CHARS:
        raise KnowledgeContentError(
            f"{path_name}: body must be {MIN_BODY_CHARS}-{MAX_BODY_CHARS} characters "
            f"(got {len(body)})"
        )

    return KnowledgeDoc(
        id=doc_id,
        title=title,
        tags=tuple(cleaned),
        language=language,
        evidence_note=evidence_note.strip(),
        reviewed=reviewed,
        body=body,
    )


def load_knowledge_docs(directory: Any | None = None) -> tuple[KnowledgeDoc, ...]:
    """Load every markdown document in the corpus directory, sorted by id.

    Raises :class:`KnowledgeContentError` if any document is malformed or two
    documents share an id — a broken document is a broken knowledge base, and
    the failure should be loud (at ingest, at first serve, and in tests) rather
    than one missing card.
    """
    root = directory if directory is not None else knowledge_dir()
    docs: list[KnowledgeDoc] = []
    seen: set[str] = set()
    for entry in sorted(root.iterdir(), key=lambda item: item.name):
        name = entry.name
        if not name.endswith(".md"):
            continue
        doc = parse_knowledge_doc(name, entry.read_text(encoding="utf-8"))
        if doc.id in seen:
            raise KnowledgeContentError(f"{name}: duplicate document id {doc.id!r}")
        seen.add(doc.id)
        docs.append(doc)
    if not docs:
        raise KnowledgeContentError("knowledge corpus is empty")
    return tuple(docs)


__all__ = [
    "KNOWLEDGE_LANGUAGES",
    "KNOWLEDGE_PACKAGE_DIR",
    "KnowledgeContentError",
    "KnowledgeDoc",
    "knowledge_dir",
    "load_knowledge_docs",
    "parse_knowledge_doc",
]
