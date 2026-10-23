"""Tests for the Prometheus /metrics exposition."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_metrics_serves_prometheus_content_type(client):
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert "version=0.0.4" in resp.headers["content-type"]


def test_metrics_counts_a_real_run(client):
    client.post("/api/tickets", json={
        "customer_email": "jordan.miles@northwind.example",
        "subject": "Charged twice for my subscription",
        "body": "Charged twice - $49.00 on the 3rd and again on the 5th.",
    })
    body = client.get("/metrics").text

    assert "# TYPE aiops_runs_total counter" in body
    assert 'aiops_runs_total{disposition="auto_resolved"} 1' in body
    assert 'aiops_runs_total{disposition="auto"} 1' in body
    assert "# TYPE aiops_pipeline_latency_ms gauge" in body
    assert 'aiops_pipeline_latency_ms{quantile="0.5"}' in body
    assert "# TYPE aiops_llm_cost_usd_total counter" in body


def test_metrics_reports_queues_and_feedback(client):
    client.post("/api/tickets", json={
        "customer_email": "jordan.miles@northwind.example",
        "subject": "I want a $1200 refund immediately",
        "body": "This is unacceptable. Refund the $1200 you double-charged me.",
    })
    run_id = client.get("/api/runs").json()[0]["id"]
    client.post(f"/api/runs/{run_id}/feedback",
                json={"run_id": run_id, "rating": "up"})

    body = client.get("/metrics").text
    assert "aiops_reviews_pending" in body
    assert 'aiops_feedback_total{rating="up"} 1' in body
    assert 'aiops_feedback_total{rating="down"} 0' in body
    assert "aiops_knowledge_chunks" in body


def test_outbox_gauge_counts_queued_items(client):
    """The pending gauge must read queue depth, not log rows.

    A queued action carries no status until first attempted, so counting
    `status == "pending"` over the raw append-only log reported 0 while
    items sat waiting — and one item retried three times read as 3.
    """
    from backend.store import storage
    storage.append("outbox", {"id": "out_g1", "kind": "email",
                              "ts": "2026-10-01T00:00:00Z"})
    body = client.get("/metrics").text
    assert "aiops_outbox_pending 1" in body

    # One item, two failed attempts -> still depth 1, never 2.
    storage.append("outbox", {"id": "out_g1", "status_update": True,
                              "status": "pending", "attempts": 1,
                              "ts": "2026-10-01T00:00:01Z"})
    storage.append("outbox", {"id": "out_g1", "status_update": True,
                              "status": "pending", "attempts": 2,
                              "ts": "2026-10-01T00:00:02Z"})
    body = client.get("/metrics").text
    assert "aiops_outbox_pending 1" in body

    # Delivered -> depth 0.
    storage.append("outbox", {"id": "out_g1", "status_update": True,
                              "status": "delivered", "attempts": 3,
                              "ts": "2026-10-01T00:00:03Z"})
    assert "aiops_outbox_pending 0" in client.get("/metrics").text


def test_outbox_gauge_counts_never_attempted_items(client):
    """An item queued before any attempt must still register as pending."""
    from backend.store import storage
    storage.append("outbox", {"id": "out_g2", "kind": "refund",
                              "ts": "2026-10-01T00:00:00Z"})
    body = client.get("/metrics").text
    assert "aiops_outbox_pending 1" in body


def test_outbox_gauge_forgets_a_reset_dead_letter(client):
    """A dead item that was reset to pending no longer counts as dead."""
    from backend.store import storage
    storage.append("outbox", {"id": "out_g3", "kind": "crm_update",
                              "ts": "2026-10-01T00:00:00Z"})
    # Dead, then operator reset:
    storage.append("outbox", {"id": "out_g3", "status_update": True,
                              "status": "dead", "attempts": 5,
                              "ts": "2026-10-01T00:00:01Z"})
    storage.append("outbox", {"id": "out_g3", "status_update": True,
                              "status": "pending", "attempts": 0,
                              "ts": "2026-10-01T00:00:02Z"})
    body = client.get("/metrics").text
    assert "aiops_outbox_pending 1" in body
    assert "aiops_outbox_pending 0" not in body
