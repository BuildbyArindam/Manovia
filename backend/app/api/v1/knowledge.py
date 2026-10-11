"""The knowledge base over HTTP: what the "Learn more" cards open.

Two endpoints, both authenticated (this is product content for the signed-in
companion, not crisis material — the crisis endpoints' no-auth rule exists so
a person in danger is never asked for credentials, and nothing here is that
urgent):

``GET /knowledge``
    The corpus summary: id, title, tags, language, evidence note and review
    status for every document. Powers a browsing surface and lets a client
    verify a retrieved id exists.

``GET /knowledge/{doc_id}``
    One document, body included, ready to render in a card. The id is the
    same one chat metadata carries in ``retrieved_ids`` /
    ``retrieved_docs[].id``.

Every response carries the same disclaimer: general self-help information,
not medical advice, not a diagnosis, not a substitute for professional care.
``reviewed`` is shipped honestly — ``false`` until the human safety review in
``docs/knowledge-authoring-guide.md`` has happened — so a client can choose
to display provenance rather than imply an endorsement the corpus does not
have.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.deps import CurrentUser
from app.content.knowledge import KnowledgeDoc, load_knowledge_docs
from app.core.errors import ApiError

router = APIRouter(prefix="/knowledge", tags=["knowledge"])

#: Shipped with every knowledge response. One constant, here, so it cannot
#: drift between the list and the detail endpoint.
DISCLAIMER: str = (
    "General self-help information curated for Manovia. It is not medical "
    "advice, not a diagnosis, and not a substitute for professional care."
)


class KnowledgeDocSummary(BaseModel):
    """One knowledge document, without its body."""

    id: str
    title: str
    tags: list[str]
    language: str
    evidence_note: str
    reviewed: bool


class KnowledgeDocOut(KnowledgeDocSummary):
    """One knowledge document, body included, with the shared disclaimer."""

    body: str
    disclaimer: str


class KnowledgeListOut(BaseModel):
    """The corpus summary plus the shared disclaimer."""

    documents: list[KnowledgeDocSummary]
    count: int
    disclaimer: str


def _summary(doc: KnowledgeDoc) -> KnowledgeDocSummary:
    return KnowledgeDocSummary(
        id=doc.id,
        title=doc.title,
        tags=list(doc.tags),
        language=doc.language,
        evidence_note=doc.evidence_note,
        reviewed=doc.reviewed,
    )


@router.get("")
async def list_knowledge(user: CurrentUser) -> KnowledgeListOut:
    """Every curated document, summary form, sorted by id."""
    docs = load_knowledge_docs()
    return KnowledgeListOut(
        documents=[_summary(doc) for doc in docs],
        count=len(docs),
        disclaimer=DISCLAIMER,
    )


@router.get("/{doc_id}")
async def get_knowledge_doc(doc_id: str, user: CurrentUser) -> KnowledgeDocOut:
    """One document, body included. 404 if the id is not in the corpus."""
    docs = load_knowledge_docs()
    for doc in docs:
        if doc.id == doc_id:
            return KnowledgeDocOut(
                **_summary(doc).model_dump(),
                body=doc.body,
                disclaimer=DISCLAIMER,
            )
    raise ApiError(404, "knowledge_not_found", "That document does not exist.")


__all__ = ["DISCLAIMER", "KnowledgeDocOut", "KnowledgeDocSummary", "KnowledgeListOut", "router"]
