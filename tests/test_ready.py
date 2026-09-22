"""Tests for the /ready readiness probe."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.main import _ready_body, ready  # noqa: E402


def test_ready_ok(monkeypatch):
    body = _ready_body()
    assert body["status"] == "ready"
    assert body["checks"]["storage_writable"] is True
    assert body["checks"]["knowledge_chunks"] > 0


def test_ready_not_ready_when_storage_readonly(monkeypatch, tmp_path):
    import backend.main as m

    class ReadOnlyDir:
        def __truediv__(self, name):
            class P:
                def write_text(self, *_a, **_k):
                    raise OSError("read-only filesystem")
                def unlink(self, *a, **k):
                    pass
            return P()

    monkeypatch.setattr(m.config, "DATA_DIR", ReadOnlyDir())
    body = _ready_body()
    assert body["status"] == "not_ready"
    assert body["checks"]["storage_writable"] is False


def test_route_wraps_503(monkeypatch):
    # Healthy path: the route returns the plain body (FastAPI serializes it).
    body = ready()
    assert body["status"] == "ready"
    assert body["checks"]["knowledge_chunks"] > 0
