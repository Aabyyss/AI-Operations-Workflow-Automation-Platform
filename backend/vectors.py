"""Vector retrieval behind one interface: in-process, or pgvector in Postgres.

The knowledge agent's retrieval has to work in three situations, and it has
to be obvious which one is running:

1. **No API key, no database** (the default, and CI): TF-IDF. Handled by
   `rag.TfidfIndex`; nothing in this module is loaded.
2. **Live embeddings, no database**: vectors live in memory for the life of
   the process. `MemoryVectorIndex`.
3. **Live embeddings, Postgres with pgvector**: vectors live in the database,
   so a restart does not re-embed the corpus and two workers share one index.
   `PgVectorIndex`.

Both vector indexes answer the same two calls — `replace(chunks, vectors)`
and `search(vector, k)` — and return the same `RetrievedChunk` objects, so
the retriever does not branch on where the numbers live. Plain cosine
similarity in memory is the reference; pgvector's `<=>` (cosine distance) is
the same arithmetic done by the database, and the score is converted back to
a similarity so a threshold means the same thing in both.

Embeddings are produced by `embed_texts`, which needs the configured
embedding model — there is no pretending a TF-IDF vector is a 1536-dim
OpenAI vector. If the model is unavailable, the caller keeps the TF-IDF
index rather than degrading quietly into nonsense.
"""
from __future__ import annotations

import math
from typing import Any, Protocol

from . import config
from .models import RetrievedChunk

EMBED_DIM = 1536   # text-embedding-3-small

_SCHEMA = """
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS embeddings (
    id          BIGSERIAL PRIMARY KEY,
    doc_id      TEXT        NOT NULL,
    chunk_index INTEGER     NOT NULL,
    title       TEXT        NOT NULL DEFAULT '',
    content     TEXT        NOT NULL,
    embedding   vector(1536),
    meta        JSONB       NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (doc_id, chunk_index)
);
CREATE INDEX IF NOT EXISTS embeddings_doc_idx ON embeddings (doc_id, chunk_index);
"""


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity, 0.0 when either vector is zero-length."""
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


class VectorIndex(Protocol):
    """What the retriever needs from a vector store. Two methods, no more."""

    def replace(self, chunks: list[dict], vectors: list[list[float]]) -> int: ...

    def search(self, vector: list[float], k: int = 4) -> list[RetrievedChunk]: ...


class MemoryVectorIndex:
    """Cosine over a list in this process. Fine for one worker, gone on restart."""

    def __init__(self) -> None:
        self._chunks: list[dict] = []
        self._vectors: list[list[float]] = []

    def replace(self, chunks: list[dict], vectors: list[list[float]]) -> int:
        if len(chunks) != len(vectors):
            raise ValueError("chunks and vectors must be the same length")
        self._chunks = [dict(c) for c in chunks]
        self._vectors = [list(v) for v in vectors]
        return len(self._chunks)

    def search(self, vector: list[float], k: int = 4) -> list[RetrievedChunk]:
        scored = [(cosine(vector, v), c) for v, c in zip(self._vectors, self._chunks)]
        scored.sort(key=lambda pair: -pair[0])
        return [RetrievedChunk(doc_id=c["doc_id"], title=c.get("title", ""),
                               text=c["text"][:900], score=round(s, 4))
                for s, c in scored[:k] if s > 0]

    def count(self) -> int:
        return len(self._chunks)


class PgVectorIndex:
    """The same index, in Postgres, so it survives a restart and is shared.

    SQL is kept boring on purpose: one DELETE-then-INSERT replace (a corpus
    reload is atomic enough for this workload and keeps the table honest),
    and one nearest-neighbour SELECT ordered by cosine distance. psycopg is
    imported lazily so the module can be unit-tested and imported on a
    machine that has neither the driver nor a server.
    """

    def __init__(self, dsn: str = "", dim: int = EMBED_DIM) -> None:
        self._dsn = dsn
        self._dim = dim
        self._conn: Any = None

    def _connect(self) -> Any:
        if self._conn is None or self._conn.closed:
            import psycopg  # lazy: only a real deployment needs the driver

            self._conn = psycopg.connect(self._dsn, autocommit=True)
        return self._conn

    def ensure_schema(self) -> None:
        conn = self._connect()
        with conn.cursor() as cur:
            cur.execute(_SCHEMA)

    def replace(self, chunks: list[dict], vectors: list[list[float]]) -> int:
        if len(chunks) != len(vectors):
            raise ValueError("chunks and vectors must be the same length")
        conn = self._connect()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM embeddings")
            if not chunks:
                return 0
            cur.executemany(
                "INSERT INTO embeddings (doc_id, chunk_index, title, content, embedding) "
                "VALUES (%s, %s, %s, %s, %s::vector) "
                "ON CONFLICT (doc_id, chunk_index) DO UPDATE "
                "SET title = EXCLUDED.title, content = EXCLUDED.content, "
                "embedding = EXCLUDED.embedding",
                [(c["doc_id"], int(c.get("chunk_index", 0)), c.get("title", ""),
                  c["text"], _vector_literal(v)) for c, v in zip(chunks, vectors)],
            )
        return len(chunks)

    def search(self, vector: list[float], k: int = 4) -> list[RetrievedChunk]:
        conn = self._connect()
        literal = _vector_literal(vector)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT doc_id, title, content, 1 - (embedding <=> %s::vector) AS score "
                "FROM embeddings ORDER BY embedding <=> %s::vector LIMIT %s",
                (literal, literal, k),
            )
            rows = cur.fetchall()
        return [RetrievedChunk(doc_id=r[0], title=r[1] or "", text=(r[2] or "")[:900],
                               score=round(float(r[3]), 4))
                for r in rows if float(r[3]) > 0]

    def count(self) -> int:
        conn = self._connect()
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM embeddings")
            row = cur.fetchone()
        return int(row[0]) if row else 0


def _vector_literal(vector: list[float]) -> str:
    """pgvector's text form: '[0.1,0.2,…]'. No driver type needed."""
    return "[" + ",".join(f"{v:.7g}" for v in vector) + "]"


def embed_texts(texts: list[str], model: str | None = None) -> list[list[float]]:
    """Embed with the configured model. Raises when there is no key —
    a rule-based system must not pretend to have produced embeddings."""
    if not config.is_live():
        raise RuntimeError("embeddings need AIOPS_MODE=live and OPENAI_API_KEY")
    from openai import OpenAI

    client = OpenAI(api_key=config.OPENAI_API_KEY, base_url=config.OPENAI_BASE_URL)
    resp = client.embeddings.create(model=model or config.EMBED_MODEL, input=texts)
    return [item.embedding for item in resp.data]


def build_vector_index(chunks: list[dict]) -> VectorIndex | None:
    """Embed the corpus and return the best available index, or None.

    None means "stay on TF-IDF". It is returned rather than raised because
    retrieval working without a provider is a feature of this platform, not
    a degraded mode to alert on.
    """
    if not config.is_live() or not chunks:
        return None
    vectors = embed_texts([c["text"] for c in chunks])
    if config.STORAGE_BACKEND == "postgres":
        if not config.DATABASE_URL:
            raise RuntimeError("AIOPS_STORAGE=postgres requires AIOPS_DATABASE_URL")
        index = PgVectorIndex(config.DATABASE_URL)
        index.ensure_schema()
    else:
        index = MemoryVectorIndex()
    index.replace(chunks, vectors)
    return index
