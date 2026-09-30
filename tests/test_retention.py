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
