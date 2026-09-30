"""Tests for run feedback capture."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402


def _run_ticket(client):
    resp = client.post("/api/tickets", json={
        "customer_email": "jordan.miles@northwind.example",
        "subject": "Charged twice for my subscription",
        "body": "I've been charged twice this month - $49.00 on the 3rd and "
                "again on the 5th. I need one of the charges refunded.",
    })
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_feedback_roundtrip(client):
    run = _run_ticket(client)

    resp = client.post(f"/api/runs/{run['id']}/feedback", json={
        "run_id": run["id"], "rating": "up", "reviewer": "asha",
    })
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["id"].startswith("fb_")
    assert body["run_id"] == run["id"]

    listing = client.get("/api/feedback").json()
    assert [f["id"] for f in listing] == [body["id"]]


def test_feedback_requires_unknown_run_404(client):
    resp = client.post("/api/runs/run_nope/feedback",
                       json={"run_id": "run_nope", "rating": "down"})
    assert resp.status_code == 404


def test_feedback_rating_is_validated(client):
    resp = client.post("/api/runs/run_x/feedback",
                       json={"run_id": "run_x", "rating": "meh"})
    assert resp.status_code == 422


def test_feedback_with_correction(client):
    run = _run_ticket(client)
    resp = client.post(f"/api/runs/{run['id']}/feedback", json={
        "run_id": run["id"], "rating": "down",
        "correction": "Refund should have been the second charge only",
    })
    assert resp.status_code == 201
    assert "second charge" in resp.json()["correction"]
