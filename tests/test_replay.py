"""Tests for run replay."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import config  # noqa: E402


def _run_ticket(client):
    resp = client.post("/api/tickets", json={
        "customer_email": "jordan.miles@northwind.example",
        "subject": "Charged twice for my subscription",
        "body": "Charged twice - $49.00 on the 3rd and again on the 5th.",
    })
    return resp.json()


def test_replay_is_deterministic_in_mock_mode(client):
    original = _run_ticket(client)

    resp = client.post(f"/api/runs/{original['id']}/replay")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["original"]["run_id"] == original["id"]
    assert body["replay"]["run_id"] != original["id"]  # a new run, not a mutation
    assert body["identical_disposition"] is True
    assert body["identical_response"] is True
    assert body["replay"]["disposition"] == "auto_resolved"


def test_replay_unknown_run_404(client):
    assert client.post("/api/runs/run_nope/replay").status_code == 404


def test_replay_when_ticket_pruned_409(client):
    run = _run_ticket(client)
    # Simulate retention having pruned the ticket store.
    (config.DATA_DIR / "tickets.json").write_text("[]", encoding="utf-8")
    resp = client.post(f"/api/runs/{run['id']}/replay")
    assert resp.status_code == 409
