"""The migration's embeddings step: explicit, and honest when it cannot run.

Moving document storage to Postgres is cheap; moving *retrieval* to pgvector
is the part that needs a provider. The script must say so and exit non-zero
rather than leaving an empty embeddings table that the retriever would then
interpret as "no policy matched" — the worst possible failure for a support
agent, because it looks like a content problem instead of a setup one.
"""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import config, vectors  # noqa: E402
from backend.rag import chunk_markdown  # noqa: E402
from scripts import migrate_to_postgres  # noqa: E402


def _cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "scripts.migrate_to_postgres", *args],
                          cwd=ROOT, capture_output=True, text=True)


def test_embeddings_need_a_model_and_say_so(monkeypatch):
    """No key means no embeddings — and a message naming the setting."""
    monkeypatch.setattr(config, "MODE", "mock")
    monkeypatch.setattr(config, "OPENAI_API_KEY", "")
    with pytest.raises(RuntimeError, match="AIOPS_MODE=live"):
        migrate_to_postgres.embed_corpus("postgresql://fake")


def test_embed_corpus_embeds_every_chunk(monkeypatch):
    chunks = []
    for path in sorted(config.KNOWLEDGE_DIR.glob("*.md")):
        chunks.extend(chunk_markdown(path))
    assert chunks, "the repo ships a knowledge corpus"

    calls = {}

    def fake_embed(texts, model=None):
        calls["texts"] = texts
        return [[0.1, 0.2]] * len(texts)

    class FakeIndex:
        def __init__(self, dsn=""):
            calls["dsn"] = dsn

        def ensure_schema(self):
            calls["schema"] = True

        def replace(self, given, vectors):
            calls["replaced"] = (len(given), len(vectors))
            return len(given)

    monkeypatch.setattr(vectors, "embed_texts", fake_embed)
    monkeypatch.setattr(vectors, "PgVectorIndex", FakeIndex)

    n = migrate_to_postgres.embed_corpus("postgresql://target")
    assert n == len(chunks)
    assert calls["dsn"] == "postgresql://target"
    assert calls["schema"] is True
    assert calls["replaced"] == (len(chunks), len(chunks))
    assert len(calls["texts"]) == len(chunks)


def test_an_empty_corpus_is_an_error_not_a_zero_record_success(monkeypatch, tmp_path):
    empty = tmp_path / "kb"
    empty.mkdir()
    monkeypatch.setattr(config, "KNOWLEDGE_DIR", empty)
    with pytest.raises(RuntimeError, match="no knowledge documents"):
        migrate_to_postgres.embed_corpus("postgresql://target")


def test_a_missing_dsn_is_still_rejected_first():
    proc = _cli("--embeddings")
    assert proc.returncode == 2
    assert "no DSN" in proc.stderr
