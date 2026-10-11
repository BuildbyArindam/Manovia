"""The knowledge endpoints end to end: auth, corpus summary, detail, 404."""

from typing import Any

import httpx


async def auth_headers(client: httpx.AsyncClient) -> dict[str, str]:
    response = await client.post("/api/v1/auth/guest")
    assert response.status_code == 201
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def test_knowledge_requires_authentication(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/v1/knowledge")).status_code == 401
    assert (await client.get("/api/v1/knowledge/sleep-when-mind-races")).status_code == 401


async def test_knowledge_lists_the_whole_corpus(client: httpx.AsyncClient) -> None:
    headers = await auth_headers(client)
    response = await client.get("/api/v1/knowledge", headers=headers)
    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    assert body["count"] == len(body["documents"])
    assert body["count"] >= 30, "the Day 13 corpus: 30-40 documents"
    assert "not medical advice" in body["disclaimer"]
    docs = body["documents"]
    by_id = {d["id"]: d for d in docs}
    assert len(by_id) == len(docs), "ids are unique"
    for doc in docs:
        assert doc["title"] and doc["language"] in {"en", "hi", "bn"}
        assert doc["reviewed"] is False, "nothing ships as reviewed before the human review"
        assert doc["evidence_note"], "provenance is always stated"
    # Coverage of the brief's topics, spot checks across languages.
    for expected in ("grounding-five-senses", "sleep-basics-rhythm", "trust-telling-someone-hi"):
        assert expected in by_id, f"{expected} missing from the corpus"


async def test_knowledge_detail_returns_the_body(client: httpx.AsyncClient) -> None:
    headers = await auth_headers(client)
    response = await client.get("/api/v1/knowledge/grounding-five-senses", headers=headers)
    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    assert body["id"] == "grounding-five-senses"
    assert body["title"]
    assert "grounding" in body["body"].lower() or "senses" in body["body"].lower()
    assert "not medical advice" in body["disclaimer"]
    for field in ("tags", "language", "evidence_note", "reviewed"):
        assert field in body


async def test_knowledge_detail_unknown_id_is_404(client: httpx.AsyncClient) -> None:
    headers = await auth_headers(client)
    response = await client.get("/api/v1/knowledge/not-a-real-doc", headers=headers)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "knowledge_not_found"
