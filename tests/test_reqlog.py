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


# ---------------------------------------------------------------- file sink
# AIOPS_REQUEST_LOG_FILE was documented in the README and the changelog and
# forwarded by docker-compose — and read by nothing. Setting it did nothing,
# silently, which is the same failure as never shipping the feature.


def test_records_are_appended_to_the_configured_file(json_client, capsys, tmp_path,
                                                     monkeypatch):
    monkeypatch.setattr(config, "REQUEST_LOG_FILE", str(tmp_path / "requests.log"))
    json_client.get("/api/tickets")
    json_client.get("/health")            # still exempt from both sinks

    log = tmp_path / "requests.log"
    assert log.exists()
    lines = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()]
    assert [r["path"] for r in lines] == ["/api/tickets"]
    # stdout keeps the same record: two sinks, one line each, same content.
    assert _http_lines(capsys) == lines


def test_appends_rather_than_truncating(json_client, capsys, tmp_path, monkeypatch):
    """Two processes, or two runs of the same process, share the file."""
    log = tmp_path / "requests.log"
    log.write_text('{"event":"http_request","prior":true}\n', encoding="utf-8")
    monkeypatch.setattr(config, "LOG_FORMAT", "json")
    monkeypatch.setattr(config, "REQUEST_LOG_FILE", str(log))

    json_client.get("/api/tickets")
    recs = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()]
    assert len(recs) == 2
    assert recs[0]["prior"] is True        # the earlier line survived
    assert recs[1]["path"] == "/api/tickets"


def test_an_unwritable_file_never_fails_the_request(json_client, capsys, tmp_path,
                                                    monkeypatch):
    """A log sink is not allowed to take the API down with it: the request
    still succeeds, stdout still has the record, and the warning appears
    exactly once — a repeating warning on every request is its own outage."""
    import backend.reqlog as reqlog

    blocked = tmp_path / "blocked"
    blocked.write_text("i am a file, not a directory", encoding="utf-8")
    monkeypatch.setattr(config, "LOG_FORMAT", "json")
    monkeypatch.setattr(config, "REQUEST_LOG_FILE", str(blocked / "requests.log"))
    monkeypatch.setattr(reqlog, "_file_warning_shown", False)

    first = json_client.get("/api/tickets")
    second = json_client.get("/api/tickets")
    assert first.status_code == 200
    assert second.status_code == 200

    captured = capsys.readouterr().out
    assert captured.count("request log file unavailable") == 1
    # both records reached stdout despite the dead file
    out_records = [json.loads(x) for x in captured.splitlines()
                   if x.strip().startswith("{") and json.loads(x).get("event") == "http_request"]
    assert len(out_records) == 2


def test_no_file_configured_means_no_file_attempt(json_client, capsys, tmp_path,
                                                  monkeypatch):
    monkeypatch.setattr(config, "LOG_FORMAT", "json")
    monkeypatch.setattr(config, "REQUEST_LOG_FILE", "")   # what compose emits
    json_client.get("/api/tickets")
    assert list(tmp_path.glob("*.log")) == []
    assert len(_http_lines(capsys)) == 1
