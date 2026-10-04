"""Tests for the knowledge-base management API."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


DOC = """# Refund Exceptions

## Duplicate charge within 24 hours
Approve refunds for duplicate charges reported within 24 hours.

## Chargeback already filed
Do not refund while a chargeback is open; escalate to finance.
"""


def test_list_and_read_seeded_corpus(client):
    docs = client.get("/api/knowledge").json()
    assert docs, "seeded corpus must be visible"
    expected = {"doc_id", "title", "chunks", "bytes", "modified_at"}
    assert all(set(d) == expected for d in docs)
    assert any(d["doc_id"] == "refund_policy" for d in docs)
    r = client.get("/api/knowledge/refund_policy")
    assert r.status_code == 200
    body = r.json()
    assert body["doc_id"] == "refund_policy"
    assert "##" in body["markdown"]


def test_save_then_pipeline_retrieves_new_policy(client):
    r = client.post("/api/knowledge", json={"name": "Refund Exceptions", "markdown": DOC})
    assert r.status_code == 201
    body = r.json()
    assert body["doc_id"] == "refund-exceptions"
    assert body["created"] is True
    assert body["chunks"] == 2  # the sub-40-char title-only intro is dropped by design
    # The live RAG index picked it up without a reload call.
    assert body["index_chunks"] > client.get("/api/knowledge").json()[0]["chunks"] - 1


def test_overwrite_requires_explicit_flag(client):
    client.post("/api/knowledge", json={"name": "doc-x", "markdown": "# A\n\n## s\nbody text here\n"})
    r = client.post("/api/knowledge", json={"name": "doc-x", "markdown": "# B\n\n## t\nother text\n"})
    assert r.status_code == 409
    r = client.post("/api/knowledge",
                    json={"name": "doc-x", "markdown": "# B\n\n## t\nother text\n", "overwrite": True})
    assert r.status_code == 201 and r.json()["created"] is False


def test_save_rejects_empty_and_bad_slugs(client):
    assert client.post("/api/knowledge", json={"name": "x", "markdown": "   "}).status_code == 422
    r = client.post("/api/knowledge", json={"name": "../evil", "markdown": DOC})
    assert r.status_code == 422
    r = client.get("/api/knowledge/..%2F..%2Fsecrets")
    assert r.status_code in (404, 422)  # never a successful read outside the corpus


def test_delete_then_404(client):
    client.post("/api/knowledge", json={"name": "temp-doc", "markdown": DOC})
    assert client.delete("/api/knowledge/temp-doc").status_code == 200
    assert client.delete("/api/knowledge/temp-doc").status_code == 404
    assert client.get("/api/knowledge/temp-doc").status_code == 404
    docs = client.get("/api/knowledge").json()
    assert all(d["doc_id"] != "temp-doc" for d in docs)


def test_saved_doc_changes_retrieval(client):
    """The whole point: a saved policy must shape the next routing decision."""
    from backend.rag import retriever

    assert client.post("/api/knowledge", json={"name": "chargeback-playbook",
                                                "markdown": DOC}).status_code == 201
    # 'chargeback already filed' is verbatim in the saved doc, not in the seed.
    hits = retriever.retrieve("chargeback already filed", k=3)
    assert hits and hits[0].doc_id == "chargeback-playbook"
