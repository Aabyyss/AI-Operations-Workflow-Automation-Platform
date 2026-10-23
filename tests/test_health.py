"""Tests for GET /api/health — the component health report."""
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.store import storage  # noqa: E402


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_reset_outbox(store, oid="oh1"):
    """A reset dead-letter: base, dead, then pending."""
    store.append("outbox", {"id": oid, "kind": "refund", "ts": iso_now()})
    store.append("outbox", {"id": oid, "status_update": True,
                             "status": "dead", "attempts": 5, "ts": iso_now()})
    store.append("outbox", {"id": oid, "status_update": True,
                             "status": "pending", "attempts": 0, "ts": iso_now()})


def test_health_report_shape_and_ok_state(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["errors"] == [] and body["degraded"] == []
    checks = body["checks"]
    for component in ("storage", "knowledge", "approval_queue", "outbox",
                      "outbound_delivery", "llm_gateway", "budget"):
        assert component in checks, f"missing component: {component}"
    assert checks["storage"]["status"] == "ok"
    assert checks["storage"]["records"]["runs"] == 0
    assert checks["knowledge"]["chunks"] > 0
    assert checks["approval_queue"]["pending_reviews"] == 0
    assert checks["outbox"]["dead_letters"] == 0
    assert checks["outbound_delivery"]["status"] == "disabled"  # no OUTBOX_URL in tests
    assert checks["llm_gateway"]["mode"] in ("mock", "live")
    assert checks["budget"]["enabled"] is False


def test_health_counts_live_records(client):
    t = {"customer_email": "a@b.com", "subject": "Charged twice",
         "body": "You charged my card twice for $49, please refund one."}
    assert client.post("/api/tickets", json=t).status_code == 200
    body = client.get("/api/health").json()
    assert body["checks"]["storage"]["records"]["runs"] >= 1
    assert body["checks"]["storage"]["records"]["tickets"] >= 1
    # The small duplicate-charge ticket auto-resolves: no backlog, still ok.
    assert body["status"] == "ok"


def test_health_flags_dead_letters_as_degraded(client, monkeypatch):
    from backend import store as store_module
    store_module.storage.append("outbox", {
        "id": "out_dead", "action": "email.send", "status": "dead",
        "attempts": 3, "created_at": "2026-01-01T00:00:00Z",
    })

    # The folded view, not the raw append-only log, decides the count.
    from backend import outbound
    assert outbound.outbox_dead_count() == 1
    body = client.get("/api/health").json()
    assert body["checks"]["outbox"]["status"] == "degraded"
    assert body["checks"]["outbox"]["dead_letters"] == 1
    assert body["status"] == "degraded"
    assert "outbox" in body["degraded"]


def test_health_forgets_a_dead_letter_once_it_is_reset(client):
    """The report reads the folded view, not the append-only history:
    otherwise an item an operator already reset keeps the deployment
    degraded forever, and the red light cannot be cleared."""
    from backend import outbound
    from backend import store as store_module
    store_module.storage.append("outbox", {
        "id": "out_dead", "kind": "email", "status": "dead",
        "attempts": 3, "ts": "2026-01-01T00:00:00Z",
    })
    assert client.get("/api/health").json()["checks"]["outbox"]["dead_letters"] == 1

    outbound.retry("out_dead")  # operator reset -> pending again
    checks = client.get("/api/health").json()["checks"]
    assert checks["outbox"]["dead_letters"] == 0
    assert checks["outbox"]["status"] == "ok"
    assert checks["outbox"]["pending_delivery"] == 1
    assert "outbox" not in client.get("/api/health").json()["degraded"]


def test_health_reads_folded_outbox_dead_letters(client):
    """A dead-then-reset item is no longer dead: the folded view, not the
    raw log, decides the red light. Mirrors the real number in
    outbound.outbox_dead_count()."""
    _append_reset_outbox(storage)
    checks = client.get("/api/health").json()["checks"]
    assert checks["outbox"]["status"] == "ok"
    assert checks["outbox"]["dead_letters"] == 0
    assert checks["outbox"]["pending_delivery"] == 1
    from backend import outbound
    assert outbound.outbox_dead_count() == 0
    assert outbound.outbox_pending_count() == 1


def test_health_counts_queued_work_as_pending(client):
    """An item queued but never attempted is still delivery pending."""
    from backend import store as store_module
    store_module.storage.append("outbox", {
        "id": "out_new", "kind": "refund", "ts": "2026-01-01T00:00:00Z",
    })
    checks = client.get("/api/health").json()["checks"]
    assert checks["outbox"]["pending_delivery"] == 1
    assert checks["outbox"]["status"] == "ok"


def test_health_flags_pending_approval_backlog(client):
    from backend import store as store_module
    store_module.storage.append("reviews", {
        "id": "rev_1", "ticket_id": "tkt_1", "status": "pending",
        "created_at": "2026-01-01T00:00:00Z",
    })
    for i in range(99):  # push the queue past the 100-pending threshold
        store_module.storage.append("reviews", {
            "id": f"rev_{i + 2}", "ticket_id": f"tkt_{i + 2}",
            "status": "pending", "created_at": "2026-01-01T00:00:00Z",
        })
    body = client.get("/api/health").json()
    assert body["checks"]["approval_queue"]["pending_reviews"] == 100
    assert body["checks"]["approval_queue"]["status"] == "degraded"


def test_health_flags_budget_alerts(client, monkeypatch):
    monkeypatch.setattr("backend.config.MONTHLY_BUDGET_USD", 0.01)
    # A little spend, straight into the usage ledger.
    from backend import store as store_module
    store_module.storage.append("usage", {
        "id": "u1", "agent": "test", "model": "m", "tokens_in": 1000,
        "tokens_out": 1000, "cost_usd": 5.0, "created_at": "2026-10-01T00:00:00Z",
    })
    body = client.get("/api/health").json()
    assert body["checks"]["budget"]["enabled"] is True
    assert body["checks"]["budget"]["status"] == "degraded"


def test_health_never_reports_unknown_disabled_state(client):
    body = client.get("/api/health").json()
    assert body["checks"]["outbound_delivery"]["status"] in ("ok", "disabled")
    assert body["checks"]["outbound_delivery"]["target_configured"] is False


def test_health_reports_the_receipt_signing_state(client):
    """Rotation keeps old receipts verifiable, so nothing forces it — which is
    why the key's age has to be visible somewhere an operator looks."""
    check = client.get("/api/health").json()["checks"]["receipts"]
    assert check["status"] == "ok"
    assert check["rotation_due"] is False
    assert check["managed_by"] in ("file", "environment")
    assert check["published_keys"] >= 0


def test_an_old_signing_key_turns_health_degraded(client, monkeypatch):
    from backend import receipts as receipts_module
    old = {"key_id": "abcd1234abcd1234", "public_key": "aa" * 32,
           "created_at": "2020-01-01T00:00:00+00:00", "retired_at": None}
    monkeypatch.setattr(receipts_module, "published_history", lambda: [old])

    body = client.get("/api/health").json()
    check = body["checks"]["receipts"]
    assert check["status"] == "degraded"
    assert check["rotation_due"] is True
    assert "rotate with" in check["note"]
    assert "receipts" in body["degraded"]
    assert body["status"] == "degraded"


def test_health_never_calls_an_env_managed_key_overdue(client, monkeypatch):
    """A key this process cannot date is not an overdue key, and it is not
    this deployment's to rotate."""
    from backend import receipts as receipts_module
    monkeypatch.setattr("backend.config.RECEIPT_KEY", "bb" * 32)
    monkeypatch.setattr(receipts_module, "key_age_days", lambda now=None: None)

    check = client.get("/api/health").json()["checks"]["receipts"]
    assert check["managed_by"] == "environment"
    assert check["rotation_due"] is False
    assert check["status"] == "ok"
    assert "not observable" in check["note"]


def test_rotation_can_be_switched_off_by_config(client, monkeypatch):
    from backend import receipts as receipts_module
    old = {"key_id": "abcd1234abcd1234", "public_key": "aa" * 32,
           "created_at": "2020-01-01T00:00:00+00:00", "retired_at": None}
    monkeypatch.setattr(receipts_module, "published_history", lambda: [old])
    monkeypatch.setattr("backend.config.RECEIPT_KEY_MAX_AGE_DAYS", 0.0)

    check = client.get("/api/health").json()["checks"]["receipts"]
    assert check["rotation_due"] is False
    assert check["rotation_due_after_days"] is None
