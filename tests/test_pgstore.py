"""Postgres backend tests — the SQL contract, no server required.

These tests exercise PostgresStorage's behavior against a stubbed
connection: every assertion is about the SQL that would run (shape,
ordering, id-keying, transactional replace) and the values that would
come back. A real-instance smoke test lives in the docs; CI stays
hermetic and offline.
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402


class FakeCursor:
    def __init__(self, conn: "FakeConnection") -> None:
        self._conn = conn

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def execute(self, sql: str, params: tuple | None = None) -> None:
        norm = " ".join(sql.split())
        self._conn.executed.append((norm, params))
        if self._conn.fail_on and self._conn.fail_on in norm:
            raise RuntimeError(f"forced failure on: {norm}")
        self._conn.last = list(self._conn.results.get(norm, []))

    def fetchone(self):
        return self._conn.last[0] if self._conn.last else None

    def fetchall(self):
        return list(self._conn.last)

    def executemany(self, sql: str, seq: list[tuple]) -> None:
        norm = " ".join(sql.split())
        self._conn.executed.append((norm, f"<{len(seq)} rows>"))
        self._conn.executemany_rows.append((norm, seq))


class FakeConnection:
    def __init__(self) -> None:
        self.closed = False
        self.executed: list[tuple[str, tuple | None]] = []
        self.executemany_rows: list[tuple[str, list[tuple]]] = []
        self.results: dict[str, list] = {}
        self.last: list = []
        self.commits = 0
        self.rollbacks = 0
        self.fail_on: str | None = None

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


@pytest.fixture()
def fake_conn():
    return FakeConnection()


@pytest.fixture()
def pg(fake_conn):
    from backend.pgstore import PostgresStorage

    s = PostgresStorage("postgresql://fake")
    s._conn = fake_conn  # bypass _connect: no driver, no server
    return s


def _payloads(*items):
    return [(v,) for v in items]


def test_append_inserts_json_payload(pg, fake_conn):
    pg.append("runs", {"id": "r1", "cost": 0.1})
    sql, params = fake_conn.executed[-1]
    assert sql.startswith("INSERT INTO documents (collection, payload)")
    assert params[0] == "runs"
    assert json.loads(params[1]) == {"id": "r1", "cost": 0.1}
    assert fake_conn.commits >= 1


def test_all_orders_by_seq_and_returns_payloads(pg, fake_conn):
    fake_conn.results[
        "SELECT payload FROM documents WHERE collection = %s ORDER BY seq"
    ] = _payloads({"id": "a"}, {"id": "b"})
    assert pg.all("runs") == [{"id": "a"}, {"id": "b"}]


def test_get_keys_on_payload_id(pg, fake_conn):
    fake_conn.results[
        "SELECT payload FROM documents WHERE collection = %s AND payload->>'id' = %s ORDER BY seq LIMIT 1"
    ] = [({"id": "x", "status": "pending"},)]
    assert pg.get("reviews", "x") == {"id": "x", "status": "pending"}
    sql, params = fake_conn.executed[-1]
    assert "payload->>'id'" in sql
    assert params == ("reviews", "x")


def test_get_missing_returns_none(pg, fake_conn):
    assert pg.get("reviews", "nope") is None


def test_update_merges_with_jsonb_concat_and_returns_row(pg, fake_conn):
    fake_conn.results[
        "UPDATE documents SET payload = payload || %s::jsonb WHERE collection = %s AND payload->>'id' = %s RETURNING payload"
    ] = [({"id": "x", "status": "approved", "note": "ok"},)]
    out = pg.update("reviews", "x", {"status": "approved"})
    assert out["status"] == "approved"
    sql, params = fake_conn.executed[-1]  # the UPDATE (commit is not recorded)
    assert "payload || %s::jsonb" in sql and "RETURNING payload" in sql
    assert json.loads(params[0]) == {"status": "approved"}


def test_replace_all_is_transactional_swap(pg, fake_conn):
    pg.replace_all("audit", [{"n": 1}, {"n": 2}])
    assert fake_conn.executed[0][0].startswith("DELETE FROM documents")
    bulk_sql, seq = fake_conn.executemany_rows[0]
    assert bulk_sql.startswith("INSERT INTO documents")
    assert len(seq) == 2
    assert json.loads(seq[1][1]) == {"n": 2}
    assert fake_conn.commits >= 1


def test_replace_all_empty_collection_just_deletes(pg, fake_conn):
    pg.replace_all("audit", [])
    assert not fake_conn.executemany_rows  # no pointless bulk insert


def test_collection_names_selects_distinct(pg, fake_conn):
    fake_conn.results["SELECT DISTINCT collection FROM documents ORDER BY collection"] = [
        ("runs",), ("tickets",)
    ]
    assert pg.collection_names() == ["runs", "tickets"]


def test_ensure_schema_creates_documents_and_survives_pgvector_failure(fake_conn):
    from backend.pgstore import PostgresStorage

    s = PostgresStorage("postgresql://fake")
    s._conn = fake_conn
    fake_conn.fail_on = "CREATE EXTENSION"
    s.ensure_schema(with_pgvector=True)  # must not raise
    assert fake_conn.rollbacks == 1
    assert any("CREATE TABLE IF NOT EXISTS documents" in sql for sql, _ in fake_conn.executed)


def test_build_storage_defaults_to_json(monkeypatch):
    import backend.store as store
    from backend.store import build_storage

    monkeypatch.setattr(store.config, "STORAGE_BACKEND", "json")
    assert isinstance(build_storage(), store.Storage)


def test_build_storage_postgres_requires_dsn(monkeypatch):
    import backend.store as store
    from backend.store import build_storage

    monkeypatch.setattr(store.config, "STORAGE_BACKEND", "postgres")
    monkeypatch.setattr(store.config, "DATABASE_URL", "")
    with pytest.raises(RuntimeError, match="AIOPS_DATABASE_URL"):
        build_storage()


def test_build_storage_postgres_needs_driver(monkeypatch):
    import backend.store as store
    from backend.store import build_storage

    monkeypatch.setattr(store.config, "STORAGE_BACKEND", "postgres")
    monkeypatch.setattr(store.config, "DATABASE_URL", "postgresql://x")
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def fake_import(name, *a, **k):
        if name == "psycopg":
            raise ImportError("no psycopg here")
        return real_import(name, *a, **k)

    monkeypatch.setattr("builtins.__import__", fake_import)
    with pytest.raises(RuntimeError, match="requirements-postgres"):
        build_storage()


def test_build_storage_postgres_constructs_lazy(monkeypatch):
    """Driver present → PostgresStorage built without dialing the network."""
    import backend.store as store
    from backend.pgstore import PostgresStorage
    from backend.store import build_storage

    monkeypatch.setattr(store.config, "STORAGE_BACKEND", "postgres")
    monkeypatch.setattr(store.config, "DATABASE_URL", "postgresql://x")
    monkeypatch.setitem(sys.modules, "psycopg", types.ModuleType("psycopg"))
    s = build_storage()
    assert isinstance(s, PostgresStorage)
    assert s._dsn == "postgresql://x"
    assert s._conn is None  # connection is lazy


def test_store_and_pgshare_collection_names():
    from backend.store import COLLECTION_NAMES, Storage

    assert len(COLLECTION_NAMES) == 11
    assert Storage().collection_names() == sorted(COLLECTION_NAMES)
