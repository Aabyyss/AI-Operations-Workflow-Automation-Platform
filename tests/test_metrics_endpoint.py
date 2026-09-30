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
