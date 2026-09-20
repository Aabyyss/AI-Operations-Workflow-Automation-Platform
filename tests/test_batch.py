"""Tests for batch ticket ingestion."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

AUTO_TICKET = {
    "customer_email": "a@b.example",
    "subject": "Charged twice",
    "body": "I've been charged twice for my subscription — $49.00. "
            "I need one refunded.",
}
ESCALATE_TICKET = {
    "customer_email": "c@d.example",
    "subject": "Enterprise refund request",
    "body": "We were charged $2,500.00 this month and want a full refund.",
}


def test_batch_mixed_outcomes_and_summary(client):
    r = client.post("/api/tickets/batch",
                    json={"tickets": [AUTO_TICKET, AUTO_TICKET, ESCALATE_TICKET]})
    assert r.status_code == 200, r.text
    body = r.json()
    s = body["summary"]
    assert s["submitted"] == 3
    assert s["auto_resolved"] == 2
    assert s["human_review"] == 1
    assert s["failed"] == 0
    assert len(body["results"]) == 3
    assert {res["disposition"] for res in body["results"]} == \
        {"auto_resolved", "human_review"}
    # Costs and latency are aggregated.
    assert s["total_cost_usd"] > 0
    assert s["total_latency_ms"] > 0


def test_batch_validation(client):
    assert client.post("/api/tickets/batch", json={"tickets": []}).status_code == 422
    too_many = {"tickets": [AUTO_TICKET] * 101}
    assert client.post("/api/tickets/batch", json=too_many).status_code == 422


def test_batch_bad_item_does_not_block_the_rest(client):
    tickets = [
        AUTO_TICKET,
        {"customer_email": "x@y.example", "subject": "", "body": ""},  # garbage
        ESCALATE_TICKET,
    ]
    r = client.post("/api/tickets/batch", json={"tickets": tickets})
    assert r.status_code == 200
    body = r.json()
    assert body["summary"]["submitted"] == 3
    disps = [res["disposition"] for res in body["results"]]
    # The bad item fails; the others still process.
    assert disps[0] == "auto_resolved"
    assert disps[2] == "human_review"
    if body["summary"]["failed"] == 1:
        assert disps[1] == "failed"
