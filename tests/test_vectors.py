"""Vector retrieval: the same two calls over memory or pgvector.

Three things are pinned here.

1. **The arithmetic agrees.** Cosine in memory and pgvector's `<=>` are the
   same measure, and both are reported as a similarity (1 - distance), so a
   configured `MIN_RETRIEVAL_SCORE` means the same thing on either backend.
2. **The SQL is what we think it is.** With a stubbed connection, every
   assertion is about the statement that would run: the extension and
   schema, the DELETE-then-INSERT replace, the ORDER BY on cosine distance,
   and the vector handed over as a pgvector literal rather than a Python
   list psycopg would have to guess about.
3. **Zero config still works.** No key, no database: `build_vector_index`
   returns None and the retriever serves TF-IDF. An embeddings failure at
   boot leaves the platform answering tickets rather than refusing to start.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import config, rag, vectors  # noqa: E402
from backend.models import RetrievedChunk  # noqa: E402


def _chunks(n=3):
    return [{"doc_id": f"doc{i}", "title": f"Doc {i}", "text": f"policy text {i}",
             "chunk_index": i} for i in range(n)]


# ------------------------------------------------------------------- cosine
def test_cosine_is_one_for_identical_and_zero_for_orthogonal():
    assert vectors.cosine([1, 2, 3], [1, 2, 3]) == pytest.approx(1.0)
    assert vectors.cosine([1, 0], [0, 1]) == pytest.approx(0.0)
    assert vectors.cosine([1, 1], [2, 2]) == pytest.approx(1.0)


def test_cosine_of_a_zero_vector_is_zero_not_a_division_error():
    assert vectors.cosine([0, 0], [1, 2]) == 0.0
    assert vectors.cosine([], [1, 2]) == 0.0


# ------------------------------------------------------------- memory index
def test_memory_index_ranks_by_similarity_and_returns_chunks():
    index = vectors.MemoryVectorIndex()
    chunks = _chunks(3)
    index.replace(chunks, [[1.0, 0.0], [0.9, 0.1], [0.0, 1.0]])
    hits = index.search([1.0, 0.0], k=2)
    assert [h.doc_id for h in hits] == ["doc0", "doc1"]
    assert hits[0].score > hits[1].score > 0
    assert isinstance(hits[0], RetrievedChunk)
    assert index.count() == 3


def test_memory_index_drops_non_positive_scores():
    index = vectors.MemoryVectorIndex()
    index.replace(_chunks(2), [[1.0, 0.0], [-1.0, 0.0]])
    assert [h.doc_id for h in index.search([1.0, 0.0])] == ["doc0"]


def test_memory_index_replace_is_a_replace_and_checks_lengths():
    index = vectors.MemoryVectorIndex()
    index.replace(_chunks(3), [[1, 0], [0, 1], [1, 1]])
    index.replace(_chunks(1), [[1, 0]])
    assert index.count() == 1
    with pytest.raises(ValueError):
        index.replace(_chunks(2), [[1, 0]])


# ------------------------------------------------------------- pgvector SQL
class FakeCursor:
    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        norm = " ".join(sql.split())
        self._conn.executed.append((norm, params))
        self._conn.last = list(self._conn.results.get(norm, []))

    def executemany(self, sql, seq):
        norm = " ".join(sql.split())
        self._conn.executed.append((norm, f"<{len(seq)} rows>"))
        self._conn.executemany_rows.append((norm, seq))

    def fetchone(self):
        return self._conn.last[0] if self._conn.last else None

    def fetchall(self):
        return list(self._conn.last)


class FakeConnection:
    def __init__(self):
        self.closed = False
        self.executed = []
        self.executemany_rows = []
        self.results = {}
        self.last = []

    def cursor(self):
        return FakeCursor(self)


@pytest.fixture()
def pg():
    index = vectors.PgVectorIndex("postgresql://fake")
    index._conn = FakeConnection()
    return index


def test_pgvector_schema_creates_extension_table_and_unique_key(pg):
    pg.ensure_schema()
    sql = pg._conn.executed[-1][0]
    assert "CREATE EXTENSION IF NOT EXISTS vector" in sql
    assert "CREATE TABLE IF NOT EXISTS embeddings" in sql
    assert "UNIQUE (doc_id, chunk_index)" in sql
    assert "vector(1536)" in sql


def test_pgvector_replace_is_delete_then_upsert(pg):
    pg.replace(_chunks(2), [[0.1, 0.2], [0.3, 0.4]])
    first_sql, _ = pg._conn.executed[0]
    assert first_sql.startswith("DELETE FROM embeddings")
    sql, rows = pg._conn.executemany_rows[0]
    assert "ON CONFLICT (doc_id, chunk_index) DO UPDATE" in sql
    assert len(rows) == 2
    assert rows[0][0] == "doc0" and rows[0][1] == 0 and rows[0][2] == "Doc 0"
    assert rows[0][4] == "[0.1,0.2]"          # a pgvector literal, not a list


def test_pgvector_search_uses_cosine_distance_and_converts_back(pg):
    sql = ("SELECT doc_id, title, content, 1 - (embedding <=> %s::vector) AS score "
           "FROM embeddings ORDER BY embedding <=> %s::vector LIMIT %s")
    pg._conn.results[sql] = [("refund_policy", "Refund Policy", "text", 0.82),
                             ("support_sla", "SLA", "text", 0.4),
                             ("junk", "Junk", "text", 0.0)]
    hits = pg.search([1.0, 2.0], k=2)
    assert [h.doc_id for h in hits] == ["refund_policy", "support_sla"]
    assert hits[0].score == 0.82
    assert hits[0].title == "Refund Policy"
    executed_sql, params = pg._conn.executed[-1]
    assert "<=>" in executed_sql and "ORDER BY" in executed_sql
    assert params == ("[1,2]", "[1,2]", 2)   # same literal twice, plus k


def test_pgvector_count(pg):
    pg._conn.results["SELECT COUNT(*) FROM embeddings"] = [(42,)]
    assert pg.count() == 42


# -------------------------------------------------------- choosing a backend
def test_zero_config_stays_on_tfidf(monkeypatch):
    monkeypatch.setattr(config, "MODE", "mock")
    assert vectors.build_vector_index(_chunks()) is None
    assert rag.retriever.backend() == "tfidf"
    assert rag.retriever.retrieve("refund policy", k=2)   # still retrieves


def test_live_without_postgres_indexes_embeddings_in_memory(monkeypatch):
    monkeypatch.setattr(config, "MODE", "live")
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(config, "STORAGE_BACKEND", "json")
    monkeypatch.setattr(vectors, "embed_texts", lambda texts, model=None: [[1.0, 0.0]] * len(texts))
    index = vectors.build_vector_index(_chunks())
    assert isinstance(index, vectors.MemoryVectorIndex)
    assert index.count() == 3


def test_live_with_postgres_builds_the_pgvector_index(monkeypatch):
    monkeypatch.setattr(config, "MODE", "live")
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(config, "STORAGE_BACKEND", "postgres")
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://fake")
    monkeypatch.setattr(vectors, "embed_texts", lambda texts, model=None: [[1.0, 0.0]] * len(texts))
    built = {}
    real_init = vectors.PgVectorIndex.__init__

    def capture(self, dsn="", dim=vectors.EMBED_DIM):
        built["dsn"] = dsn
        self._dsn, self._dim, self._conn = dsn, dim, FakeConnection()

    monkeypatch.setattr(vectors.PgVectorIndex, "__init__", capture)
    try:
        index = vectors.build_vector_index(_chunks(1))
    finally:
        monkeypatch.setattr(vectors.PgVectorIndex, "__init__", real_init)
    assert isinstance(index, vectors.PgVectorIndex)
    assert built["dsn"] == "postgresql://fake"
    assert index.count() == 0  # replace ran against the fake connection


def test_live_without_a_key_returns_none_rather_than_fake_vectors(monkeypatch):
    monkeypatch.setattr(config, "MODE", "live")
    monkeypatch.setattr(config, "OPENAI_API_KEY", "")
    with pytest.raises(RuntimeError, match="AIOPS_MODE=live"):
        vectors.embed_texts(["anything"])


# ----------------------------------------------------------- retriever wiring
def test_retriever_prefers_the_vector_index_when_it_has_one():
    class Stub:
        def search(self, vector, k=4):
            return [RetrievedChunk(doc_id="from-vectors", title="V", text="t", score=0.9)]

    rag.retriever.vectors = Stub()
    try:
        import backend.rag as rag_module
        original = rag_module.embed_texts
        rag_module.embed_texts = lambda texts: [[1.0, 0.0]]
        try:
            hits = rag.retriever.retrieve("refund policy", k=1)
        finally:
            rag_module.embed_texts = original
    finally:
        rag.retriever.vectors = None
    assert hits[0].doc_id == "from-vectors"


def test_retriever_falls_back_to_tfidf_when_embeddings_fail(monkeypatch):
    """An embeddings outage is a bad afternoon, not an outage of the product."""

    class Broken:
        def search(self, vector, k=4):
            raise RuntimeError("provider down")

    rag.retriever.vectors = Broken()
    try:
        hits = rag.retriever.retrieve("charged twice for my subscription", k=3)
    finally:
        rag.retriever.vectors = None
    assert hits and hits[0].score > 0


def test_a_failed_index_build_at_boot_is_not_fatal(monkeypatch):
    monkeypatch.setattr(config, "MODE", "live")
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(rag, "build_vector_index",
                        lambda docs: (_ for _ in ()).throw(RuntimeError("no pgvector")))
    count = rag.retriever.reload()
    assert count > 0
    assert rag.retriever.vectors is None
    assert rag.retriever.backend() == "tfidf"


def test_readiness_reports_which_retriever_is_serving(client):
    body = client.get("/ready").json()
    assert body["checks"]["retrieval_backend"] == "tfidf"
