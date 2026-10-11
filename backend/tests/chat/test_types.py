"""Chat metadata unit tests: the retrieved-docs lockstep validator (Day 13)."""

import pytest
from pydantic import ValidationError

from app.services.chat.types import ChatMetadata, KnowledgeDocRef, ResponseType


def metadata(**overrides: object) -> ChatMetadata:
    base: dict[str, object] = {
        "risk_level": "none",
        "response_type": ResponseType.NORMAL,
    }
    base.update(overrides)
    return ChatMetadata(**base)  # type: ignore[arg-type]


def test_empty_by_default() -> None:
    md = metadata()
    assert md.retrieved_docs == []
    assert md.retrieved_ids == []


def test_matching_lists_accepted() -> None:
    docs = [KnowledgeDocRef(id="worry-time-box", title="A window for worries")]
    md = metadata(retrieved_docs=docs, retrieved_ids=["worry-time-box"])
    assert md.retrieved_ids == ["worry-time-box"]


def test_mismatched_ids_rejected() -> None:
    docs = [KnowledgeDocRef(id="worry-time-box", title="A window for worries")]
    with pytest.raises(ValidationError, match=r"lockstep|retrieved_ids"):
        metadata(retrieved_docs=docs, retrieved_ids=["something-else"])


def test_length_mismatch_rejected() -> None:
    docs = [
        KnowledgeDocRef(id="a", title="A"),
        KnowledgeDocRef(id="b", title="B"),
    ]
    with pytest.raises(ValidationError):
        metadata(retrieved_docs=docs, retrieved_ids=["a"])


def test_order_matters() -> None:
    docs = [
        KnowledgeDocRef(id="a", title="A"),
        KnowledgeDocRef(id="b", title="B"),
    ]
    with pytest.raises(ValidationError):
        metadata(retrieved_docs=docs, retrieved_ids=["b", "a"])
    md = metadata(retrieved_docs=docs, retrieved_ids=["a", "b"])
    assert md.retrieved_ids == ["a", "b"]
