"""Tests for the RAG layer: chunking, indexing, retrieval quality."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.rag import TfidfIndex, chunk_markdown, retriever  # noqa: E402


def test_chunking_splits_on_headings():
    from backend import config
    path = config.KNOWLEDGE_DIR / "refund_policy.md"
    chunks = chunk_markdown(path)
    assert len(chunks) >= 4
    assert all(c["doc_id"] == "refund_policy" for c in chunks)
    assert any("Duplicate charges" in c["title"] for c in chunks)


def test_index_ranks_relevant_doc_first():
    docs = [
        {"doc_id": "refund", "title": "Refund Policy", "text": "duplicate charge refund processing within 5-7 business days", "chunk_index": 0},
        {"doc_id": "pricing", "title": "Pricing", "text": "starter plan costs 29 dollars per user monthly billing", "chunk_index": 0},
    ]
    idx = TfidfIndex()
    idx.fit(docs)
    hits = idx.search("duplicate charge refund please", k=2)
    assert hits[0][0] == 0  # refund doc wins
    assert hits[0][1] > 0


def test_retriever_finds_refund_policy_for_duplicate_charge():
    hits = retriever.retrieve("charged twice for my subscription, need a refund", k=3)
    assert hits, "retrieval returned nothing"
    assert hits[0].doc_id == "refund_policy"
    assert hits[0].score >= 0.15


def test_retriever_finds_account_runbook_for_password_reset():
    hits = retriever.retrieve("I forgot my password and my account is locked", k=3)
    assert hits[0].doc_id == "account_runbook"


def test_retriever_finds_pricing_for_plan_question():
    hits = retriever.retrieve("how much does the growth plan cost per user", k=3)
    assert hits[0].doc_id == "pricing_plans"
