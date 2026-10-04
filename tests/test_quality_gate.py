"""Tests for quality-gate precision / recall-proxy metrics."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.quality_gate import gate_quality  # noqa: E402


def test_zero_state_is_none_not_zero():
    r = gate_quality([], [], [])
    assert r["gate_precision"] is None
    assert r["gate_recall_proxy"] is None
    assert r["escalations_decided"] == 0


def test_precision_math():
    reviews = [
        {"status": "approved", "ticket_id": "t1"},
        {"status": "rejected", "ticket_id": "t2"},
        {"status": "rejected", "ticket_id": "t3"},
        {"status": "pending", "ticket_id": "t4"},   # undecided: excluded
    ]
    r = gate_quality(reviews, [], [])
    assert r["escalations_decided"] == 3
    assert r["escalations_pending"] == 1
    assert r["reviewer_approved"] == 1
    assert r["reviewer_rejected"] == 2
    assert r["gate_precision"] == round(2 / 3, 3)


def test_recall_proxy_counts_thumbs_down_auto_resolutions():
    runs = [
        {"id": "run_1", "disposition": "auto_resolved"},
        {"id": "run_2", "disposition": "auto_resolved"},
        {"id": "run_3", "disposition": "auto_resolved"},
        {"id": "run_4", "disposition": "human_review"},  # not the gate's call
    ]
    feedback = [{"run_id": "run_1", "rating": "down"},
                {"run_id": "run_4", "rating": "down"}]  # escalation downvote: ignored
    r = gate_quality([], runs, feedback)
    assert r["auto_resolved_total"] == 3
    assert r["flagged_by_feedback"] == 1
    assert r["gate_recall_proxy"] == round(1 / 3, 3)


def test_endpoint_on_live_data(client):
    # Small duplicate charge auto-resolves; big refund escalates.
    client.post("/api/tickets", json={
        "customer_email": "a@b.com", "subject": "Charged twice",
        "body": "You charged my card twice for $49, please refund one."})
    client.post("/api/tickets", json={
        "customer_email": "b@b.com", "subject": "Charged twice",
        "body": "Refund $600 for the duplicate charge on my card."})
    reviews = client.get("/api/reviews").json()
    assert reviews, "big refund must land in the human queue"
    r = client.get("/api/analytics/quality").json()
    assert r["auto_resolved_total"] == 1
    assert r["escalations_pending"] == 1
    assert r["gate_precision"] is None  # nothing decided yet
    assert "recall proxy" in r["note"]
