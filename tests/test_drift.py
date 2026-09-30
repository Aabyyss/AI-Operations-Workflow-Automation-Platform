"""Tests for the drift canary."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.drift import drift_report  # noqa: E402


def _runs(spec: dict[str, int]) -> list[dict]:
    out = []
    for disp, n in spec.items():
        out.extend([{"disposition": disp}] * n)
    return out


def test_insufficient_data_state():
    r = drift_report(_runs({"auto_resolved": 10}), window=50)
    assert r["status"] == "insufficient_data"
    assert r["required"] == 100


def test_nominal_when_behavior_stable():
    # Interleave so any 50-run window sees the same mix — contiguity would
    # make the baseline and recent windows genuinely different, not stable.
    pattern = _runs({"auto_resolved": 35, "human_review": 10, "failed": 5})
    runs = pattern + pattern
    r = drift_report(runs, window=50)
    assert r["status"] == "nominal"
    assert all(not s["alert"] for s in r["signals"].values())


def test_alert_on_failure_spike():
    baseline = _runs({"auto_resolved": 45, "human_review": 5})          # 0% failures
    recent = _runs({"auto_resolved": 30, "human_review": 5, "failed": 15})  # 30%
    r = drift_report(baseline + recent, window=50)
    assert r["status"] == "alert"
    assert r["signals"]["failure"]["alert"] is True
    assert r["signals"]["failure"]["delta_pct"] == 30.0


def test_window_is_clamped_in_endpoint(client):
    resp = client.get("/api/analytics/drift", params={"window": 99999})
    assert resp.status_code == 200
    body = resp.json()
    # Either a clamped window or the insufficient-data shape — never a 500.
    assert body.get("window", 0) <= 500 or body["status"] == "insufficient_data"
