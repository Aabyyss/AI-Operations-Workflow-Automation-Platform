"""Tests for structured JSON request logging."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from backend import config  # noqa: E402


def _http_lines(capsys) -> list[dict]:
    out = capsys.readouterr().out
    recs = []
    for line in out.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        rec = json.loads(line)
        if rec.get("event") == "http_request":
            recs.append(rec)
    return recs


@pytest.fixture()
def json_client(monkeypatch):
    monkeypatch.setattr(config, "LOG_FORMAT", "json")
    from fastapi.testclient import TestClient
    from backend.main import app
    with TestClient(app) as c:
        yield c


def test_off_by_default(client, capsys):
    client.get("/api/tickets")
    assert _http_lines(capsys) == []


def test_json_line_shape(json_client, capsys):
    json_client.get("/api/tickets")
    recs = _http_lines(capsys)
    assert len(recs) == 1
    rec = recs[0]
    assert rec["method"] == "GET"
    assert rec["path"] == "/api/tickets"
    assert rec["status"] == 200
    assert rec["duration_ms"] >= 0
    assert rec["request_id"]


def test_request_id_propagated(json_client, capsys):
    r = json_client.get("/api/tickets", headers={"X-Request-ID": "my-trace-42"})
    assert r.headers["X-Request-ID"] == "my-trace-42"
    assert _http_lines(capsys)[0]["request_id"] == "my-trace-42"


def test_health_not_logged(json_client, capsys):
    json_client.get("/health")
    assert _http_lines(capsys) == []


def test_401_is_logged(json_client, capsys, monkeypatch):
    monkeypatch.setattr(config, "API_KEY", "k")
    json_client.get("/api/tickets")  # no key -> 401
    recs = _http_lines(capsys)
    assert len(recs) == 1 and recs[0]["status"] == 401
