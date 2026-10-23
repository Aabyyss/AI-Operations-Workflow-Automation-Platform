"""API contract tests via FastAPI TestClient."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_analyze_endpoint(client):
    payload = {
        "name": "Invoice Processing",
        "department": "finance",
        "monthly_volume": 2000,
        "hourly_rate_usd": 30,
        "steps": [
            {"name": "Read invoice", "minutes_per_item": 2, "repetitive": True, "structured_data": True},
            {"name": "Approve payout", "minutes_per_item": 3, "repetitive": True,
             "touches_money": True, "structured_data": True},
        ],
    }
    r = client.post("/api/analyze", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["score"]["total"] > 0
    assert body["costs"]["monthly_savings_usd"] >= 0
    assert body["costs"]["current_monthly_cost_usd"] == 5000.0  # 2000*5min/60*$30
    assert len(body["ai_mapping"]) == 2


def test_full_ticket_and_approval_flow(client):
    # 1. Large refund ticket -> human review
    r = client.post("/api/tickets", json={
        "customer_email": "big@corp.example",
        "subject": "Refund for annual plan",
        "body": "I was charged $1,200.00 and need a full refund now.",
    })
    assert r.status_code == 200
    result = r.json()
    assert result["disposition"] == "human_review"
    review_id = result["review_id"]

    # 2. Queue shows pending review
    queue = client.get("/api/reviews").json()
    pending = [x for x in queue if x["id"] == review_id]
    assert pending and pending[0]["status"] == "pending"

    # 3. Approve -> actions execute
    d = client.post(f"/api/reviews/{review_id}/decision",
                    json={"reviewer": "manager_amy", "note": "APPROVE"}).json()
    assert d["review"]["status"] == "approved"
    assert any(a.startswith("email_sent") for a in d["executed_actions"])

    # 4. Run record updated
    run = client.get(f"/api/tickets/{result['ticket_id']}").json()
    assert run["disposition"] == "approved_executed"

    # 5. Reject path on a second escalation
    r2 = client.post("/api/tickets", json={
        "customer_email": "other@corp.example",
        "subject": "Legal threat",
        "body": "If you don't refund $900 immediately I will contact my lawyer.",
    })
    review2 = r2.json()["review_id"]
    d2 = client.post(f"/api/reviews/{review2}/decision",
                     json={"reviewer": "legal", "note": "REJECT"}).json()
    assert d2["review"]["status"] == "rejected"


def test_analytics_summary(client):
    client.post("/api/tickets/demo")
    s = client.get("/api/analytics/summary").json()
    assert s["tickets_processed"] >= 1
    assert 0 <= s["automation_rate_pct"] <= 100
    assert isinstance(s["llm_cost_by_agent"], list)
    assert s["mode"] == "mock"
    assert s["knowledge_chunks"] > 0


def test_demo_ticket_auto_resolves(client):
    r = client.post("/api/tickets/demo")
    assert r.status_code == 200
    assert r.json()["disposition"] == "auto_resolved"


def test_runs_feed(client):
    client.post("/api/tickets/demo")
    runs = client.get("/api/runs").json()
    assert len(runs) == 1
    assert runs[0]["id"].startswith("run_")
    assert runs[0]["disposition"] == "auto_resolved"
    assert runs[0]["intake"]["category"] == "billing"


def test_audit_log_records_events(client):
    client.post("/api/tickets/demo")
    events = [e["event"] for e in client.get("/api/audit").json()]
    assert "auto_resolved" in events
    assert "process_analyzed" not in events  # not analyzed yet
    client.post("/api/analyze", json={
        "name": "P", "monthly_volume": 100, "hourly_rate_usd": 20,
        "steps": [{"name": "s", "minutes_per_item": 1, "repetitive": True}]})
    events = [e["event"] for e in client.get("/api/audit").json()]
    assert "process_analyzed" in events


def test_list_analyses_returns_what_analyze_stored(client):
    """GET /api/analyses had no direct contract test — the read side of
    Side A's store, which the dashboard's analysis list renders from."""
    assert client.get("/api/analyses").json() == []
    payload = {
        "name": "Refund Handling", "department": "support",
        "monthly_volume": 500, "hourly_rate_usd": 25,
        "steps": [{"name": "Review refund", "minutes_per_item": 3,
                   "touches_money": True}],
    }
    client.post("/api/analyze", json=payload)
    rows = client.get("/api/analyses").json()
    assert len(rows) == 1
    assert rows[0]["name"] == "Refund Handling"
    assert "score" in rows[0] and "costs" in rows[0]


def test_budget_endpoint_disabled_by_default(client, monkeypatch):
    """GET /api/analytics/budget: the endpoint shape operators alert on.
    Disabled (budget 0) is the honest default state, not an error."""
    monkeypatch.setattr("backend.config.MONTHLY_BUDGET_USD", 0.0)
    body = client.get("/api/analytics/budget").json()
    assert body["enabled"] is False
    assert body.get("alert") in (False, None)


def test_outbox_deliver_endpoint_wraps_the_flusher(client, monkeypatch):
    """POST /api/outbox/deliver is the cron/n8n entry point for the whole
    outbox — it must answer even when delivery is unconfigured, saying why
    it did nothing instead of looking like a queue that vanished."""
    from backend import config
    monkeypatch.setattr(config, "OUTBOX_URL", "")
    r = client.post("/api/outbox/deliver")
    assert r.status_code == 200
    body = r.json()
    assert body["attempted"] == 0
    assert body["waiting"] == 0
    assert "not configured" in body["reason"]
