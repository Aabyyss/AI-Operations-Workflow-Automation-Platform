"""Postgres persistence behind the same Storage interface.

Activated with `AIOPS_STORAGE=postgres` + `AIOPS_DATABASE_URL`. JSON files
stay the default: the demo, the tests and CI never need a database.

Design, mirrored on store.Storage's exact semantics:

- one `documents` table: (seq BIGSERIAL, collection TEXT, payload JSONB).
  `seq` preserves JSON-file insertion order for `all()`.
- `get`/`update` key on `payload->>'id'`, matching Storage's "find the item
  whose id field matches" behavior, and return the first match in
  insertion order.
- `update` merges shallowly with JSONB concatenation — the same contract as
  `dict.update(patch)`.
- `replace_all` is a single transaction (DELETE + bulk INSERT), which is
  what retention pruning needs to be atomic.
- schema bootstrap also provisions the pgvector extension and an
  `embeddings` table so the live-mode retriever can cut over without a
  second migration; failing to create them (no pgvector, no superuser)
  is non-fatal for document storage.

psycopg is imported lazily (never at module import), so unit tests can
exercise the SQL contract with a stubbed connection — no driver, no server.
"""
from __future__ import annotations

import json
import threading
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    seq        BIGSERIAL PRIMARY KEY,
    collection TEXT        NOT NULL,
    payload    JSONB       NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS documents_collection_idx
    ON documents (collection, seq);
CREATE INDEX IF NOT EXISTS documents_payload_id_idx
    ON documents (collection, (payload->>'id'));
"""

_PGVECTOR_SCHEMA = """
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS embeddings (
    id          BIGSERIAL PRIMARY KEY,
    doc_id      TEXT        NOT NULL,
    chunk_index INTEGER     NOT NULL,
    content     TEXT        NOT NULL,
    embedding   vector(1536),
    meta        JSONB       NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS embeddings_doc_idx ON embeddings (doc_id, chunk_index);
"""


class PostgresStorage:
    """Same seven-method contract as store.Storage, over one Postgres table."""

    def __init__(self, dsn: str = "") -> None:
        self._dsn = dsn
        self._conn: Any = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------ connection
    def _connect(self) -> Any:
        if self._conn is None or self._conn.closed:
            import psycopg  # lazy: only a real deployment needs the driver

            self._conn = psycopg.connect(self._dsn, autocommit=False)
        return self._conn

    def ensure_schema(self, with_pgvector: bool = False) -> None:
        """Create the documents table (+ optionally pgvector artifacts).

        pgvector failure is swallowed on purpose: document storage must not
        depend on the extension being installed or on superuser rights.
        """
        with self._lock:
            conn = self._connect()
            with conn.cursor() as cur:
                cur.execute(_SCHEMA)
                conn.commit()
                if with_pgvector:
                    try:
                        cur.execute(_PGVECTOR_SCHEMA)
                        conn.commit()
                    except Exception:
                        conn.rollback()

    # --------------------------------------------------------------- the interface
    def append(self, name: str, item: dict[str, Any]) -> None:
        with self._lock:
            conn = self._connect()
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO documents (collection, payload) VALUES (%s, %s)",
                    (name, json.dumps(item)),
                )
                conn.commit()

    def all(self, name: str) -> list[dict[str, Any]]:
        with self._lock:
            conn = self._connect()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT payload FROM documents WHERE collection = %s ORDER BY seq",
                    (name,),
                )
                return [row[0] for row in cur.fetchall()]

    def get(self, name: str, item_id: str) -> dict[str, Any] | None:
        with self._lock:
            conn = self._connect()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT payload FROM documents "
                    "WHERE collection = %s AND payload->>'id' = %s "
                    "ORDER BY seq LIMIT 1",
                    (name, item_id),
                )
                row = cur.fetchone()
                return row[0] if row else None

    def update(self, name: str, item_id: str,
               patch: dict[str, Any]) -> dict[str, Any] | None:
        with self._lock:
            conn = self._connect()
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE documents SET payload = payload || %s::jsonb "
                    "WHERE collection = %s AND payload->>'id' = %s "
                    "RETURNING payload",
                    (json.dumps(patch), name, item_id),
                )
                row = cur.fetchone()
                conn.commit()
                return row[0] if row else None

    def collection_names(self) -> list[str]:
        with self._lock:
            conn = self._connect()
            with conn.cursor() as cur:
                cur.execute("SELECT DISTINCT collection FROM documents ORDER BY collection")
                return [row[0] for row in cur.fetchall()]

    def replace_all(self, name: str, items: list[dict[str, Any]]) -> None:
        """Atomically swap a collection (retention pruning)."""
        with self._lock:
            conn = self._connect()
            with conn.cursor() as cur:
                cur.execute("DELETE FROM documents WHERE collection = %s", (name,))
                if items:
                    cur.executemany(
                        "INSERT INTO documents (collection, payload) VALUES (%s, %s)",
                        [(name, json.dumps(item)) for item in items],
                    )
                conn.commit()
