"""Tests for retention pruning."""
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import config  # noqa: E402
from backend.retention import select_for_prune  # noqa: E402

NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)


def _rec(rid, days_old, **extra):
    from datetime import timedelta
    ts = (NOW - timedelta(days=days_old)).isoformat()
    return {"id": rid, "created_at": ts, **extra}


def test_selection_by_age_and_count():
    records = [_rec(f"old{i}", 120) for i in range(10)] + \
              [_rec(f"new{i}", 1) for i in range(3)]
    keep, prune = select_for_prune(records, max_age_days=90, max_records=4, now=NOW)
    ids_kept = {r["id"] for r in keep}
    ids_pruned = {r["id"] for r in prune}
    # Fresh records always survive; the aged set keeps its 4 newest as a
    # buffer against clock errors, and the remaining 6 are pruned.
    assert all(f"new{i}" in ids_kept for i in range(3))
    assert len(ids_pruned) == 6
    assert all(i.startswith("old") for i in ids_pruned)


def test_unparseable_timestamps_are_kept():
    records = [{"id": "weird", "created_at": "not-a-date"},
               _rec("old1", 200)]
    keep, prune = select_for_prune(records, max_age_days=90, max_records=0, now=NOW)
    assert "weird" in {r["id"] for r in keep}
    assert "old1" in {r["id"] for r in prune}


def test_prune_endpoint_dry_run_then_execute(client, monkeypatch):
    from backend.retention import COLLECTION_LIMITS
    # Tighten the feedback aged-buffer to 0 so a single old record prunes.
    monkeypatch.setitem(COLLECTION_LIMITS, "feedback", (180, 0))
    old = _rec("fb_old", 400)
    fresh = _rec("fb_new", 1)
    storage_records = [old, fresh]
    import json
    (config.DATA_DIR / "feedback.json").write_text(
        json.dumps(storage_records), encoding="utf-8")

    dry = client.post("/api/admin/prune").json()
    assert dry["mode"] == "dry-run"
    assert (config.DATA_DIR / "feedback.json").read_text().count("fb_old") == 1

    done = client.post("/api/admin/prune?confirm=true").json()
    assert done["mode"] == "executed"
    remaining = json.loads((config.DATA_DIR / "feedback.json").read_text())
    assert [r["id"] for r in remaining] == ["fb_new"]


def test_pruning_never_touches_signed_receipts(client, monkeypatch):
    """A receipt is evidence someone else holds a copy of. Pruning this
    deployment's copy would break the chain an auditor verifies against — so
    `receipts` must stay out of the policy even when everything else is pruned
    to nothing."""
    from backend.retention import COLLECTION_LIMITS
    ticket = {"customer_email": "a@b.com", "subject": "Charged twice",
              "body": "You charged my card twice for $49, please refund one."}
    client.post("/api/tickets", json=ticket)
    before = client.get("/api/receipts").json()
    assert before, "the ticket should have produced a signed receipt"

    # Prune everything the policy does cover, aggressively.
    for collection in list(COLLECTION_LIMITS):
        monkeypatch.setitem(COLLECTION_LIMITS, collection, (0, 0))
    assert client.post("/api/admin/prune?confirm=true").json()["mode"] == "executed"

    after = client.get("/api/receipts").json()
    assert [r["payload_sha256"] for r in after] == [r["payload_sha256"] for r in before]
    assert client.get("/api/receipts/verify").json()["ok"] is True


def test_prune_keeps_tickets_that_surviving_runs_reference(client, monkeypatch):
    """The retention contract: pruning never orphans a run.

    DATA_MODEL and the retention docstring both promise tickets referenced
    by surviving runs are kept so replay still works — before this test,
    nothing enforced it, and a tickets prune could turn
    `POST /api/runs/{id}/replay` into a 409 for history that still exists.
    """
    import json
    from datetime import timedelta

    from backend.retention import COLLECTION_LIMITS
    # Only the aged tickets buffer is tightened: the aged set would lose
    # everything beyond its newest 0 records — unless protection applies.
    monkeypatch.setitem(COLLECTION_LIMITS, "tickets", (90, 0))

    r = client.post("/api/tickets", json={
        "customer_email": "a@b.com", "subject": "Charged twice",
        "body": "Charged twice - $49.00 on the 3rd and again on the 5th. "
                "Please refund one."})
    assert r.status_code == 200
    ticket_id = r.json()["ticket_id"]
    run = client.get("/api/runs").json()[-1]
    assert run["ticket_id"] == ticket_id

    # Age the ticket past the 90-day cutoff; its run stays fresh.
    path = config.DATA_DIR / "tickets.json"
    records = json.loads(path.read_text(encoding="utf-8"))
    for rec in records:
        if rec["id"] == ticket_id:
            rec["created_at"] = (NOW - timedelta(days=400)).isoformat()
    path.write_text(json.dumps(records), encoding="utf-8")

    dry = client.post("/api/admin/prune").json()
    assert dry["would_prune"]["tickets"] == 0   # protected, not pruned

    done = client.post("/api/admin/prune?confirm=true").json()
    assert done["pruned"]["tickets"] == 0
    kept = json.loads(path.read_text(encoding="utf-8"))
    assert ticket_id in {t["id"] for t in kept}

    # The promise's observable outcome: replay still executes.
    replay = client.post(f"/api/runs/{run['id']}/replay")
    assert replay.status_code == 200
