"""Tests for approval-queue SLA metrics."""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.analytics import approval_sla_metrics  # noqa: E402

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)


def _review(created_min_ago: float, status: str = "pending",
            reviewed_min_after: float | None = None) -> dict:
    created = NOW - timedelta(minutes=created_min_ago)
    r = {"created_at": created.isoformat(), "status": status,
         "risk_score": 0.7, "reason": "test", "proposed_response": "x"}
    if reviewed_min_after is not None:
        r["reviewed_at"] = (created + timedelta(minutes=reviewed_min_after)).isoformat()
    return r


def test_aging_buckets_and_oldest():
    reviews = [
        _review(5),      # under 15m
        _review(30),     # 15m–1h
        _review(120),    # 1h–4h
        _review(400),    # over 4h
        _review(500),    # over 4h
    ]
    m = approval_sla_metrics(reviews, runs_count=100, now=NOW)
    assert m["pending"] == 5
    assert m["aging_buckets"] == {"under_15m": 1, "15m_to_1h": 1,
                                  "1h_to_4h": 1, "over_4h": 2}
    assert m["oldest_pending_minutes"] == 500.0


def test_turnaround_stats():
    reviews = [
        _review(60, "approved", reviewed_min_after=10),
        _review(120, "rejected", reviewed_min_after=30),
        _review(240, "approved", reviewed_min_after=50),
    ]
    m = approval_sla_metrics(reviews, runs_count=10, now=NOW)
    t = m["turnaround_minutes"]
    assert t["sample_size"] == 3
    assert t["mean"] == 30.0
    assert t["median"] == 30.0
    assert t["max"] == 50.0


def test_escalation_rate_and_zero_state():
    m = approval_sla_metrics([], runs_count=0, now=NOW)
    assert m["escalation_rate_pct"] == 0.0
    assert m["turnaround_minutes"]["median"] == 0.0

    reviews = [_review(5), _review(20, "approved", reviewed_min_after=15)]
    m2 = approval_sla_metrics(reviews, runs_count=4, now=NOW)
    assert m2["escalation_rate_pct"] == 50.0  # 2 reviews / 4 tickets


def test_endpoint_reports_live_queue(client):
    # One auto-resolved run + one escalated (still pending) review:
    # a $1,200 refund trips the monetary approval limit ($500).
    client.post("/api/tickets/demo")
    client.post("/api/tickets", json={
        "customer_email": "a@b.example",
        "subject": "I was charged $1,200 by mistake",
        "body": "Your system charged my card $1,200 twice this month. "
                "I need a full refund of $1,200 to my card right away.",
    })
    m = client.get("/api/analytics/approvals").json()
    assert m["tickets_processed"] == 2
    assert m["pending"] >= 1
    assert m["escalation_rate_pct"] == 50.0
    assert set(m["aging_buckets"]) == {"under_15m", "15m_to_1h", "1h_to_4h", "over_4h"}
