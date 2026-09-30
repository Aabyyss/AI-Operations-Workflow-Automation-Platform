"""Tests for the SSE runs stream."""
import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _post_ticket(client, subject: str) -> dict:
    resp = client.post("/api/tickets", json={
        "customer_email": "jordan.miles@northwind.example",
        "subject": subject,
        "body": "Charged twice - $49.00 on the 3rd and again on the 5th.",
    })
    return resp.json()


def test_stream_emits_new_runs_as_sse(client):
    first = _post_ticket(client, "Stream probe one")

    with client.stream("GET", "/api/runs/stream", params={"limit": 1}) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")

        frames: list[str] = []
        for line in resp.iter_lines():
            if line:
                frames.append(line)
            if any(f.startswith("data: ") for f in frames):
                break

    data_line = next(f for f in frames if f.startswith("data: "))
    payload = json.loads(data_line[len("data: "):])
    assert payload["id"]
    assert payload["disposition"] in {"auto_resolved", "human_review"}
    assert "total_cost_usd" in payload


def test_stream_closes_after_limit(client):
    _post_ticket(client, "Limit probe")

    with client.stream("GET", "/api/runs/stream", params={"limit": 2}) as resp:
        body = b""
        for chunk in resp.iter_bytes():
            body += chunk
            if body.count(b"event: run") >= 2:
                break
    assert body.count(b"event: run") >= 1
