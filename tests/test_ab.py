"""Tests for A/B cycle-time analytics (ai_assisted vs manual)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.ab_testing import ab_report  # noqa: E402


def test_zero_state_is_honest():
    r = ab_report([], [])
    assert r["ai_assisted"]["count"] == 0
    assert r["manual"]["count"] == 0
    assert r["sample_sufficient"] is False
    assert r["ai_faster_pct"] is None


def test_distribution_math_interpolates_median():
    runs = [{"cycle_seconds": s} for s in (10, 20, 30, 40)]
    manual = [{"cycle_seconds": s} for s in (60, 70, 80, 90, 100)]
    r = ab_report(runs, manual)
    # Median of 10..40 interpolates to 25; manual 5 samples → median 80.
    assert r["ai_assisted"]["median_seconds"] == 25.0
    assert r["manual"]["median_seconds"] == 80.0
    assert r["sample_sufficient"] is False  # ai cohort below 5
    assert r["ai_faster_pct"] is None


def test_delta_published_only_with_enough_samples():
    ai = [{"cycle_seconds": 30 + i} for i in range(6)]
    manual = [{"cycle_seconds": 120 + i} for i in range(6)]
    r = ab_report(ai, manual)
    assert r["sample_sufficient"] is True
    assert r["median_delta_seconds"] == 90.0   # 122.5 - 32.5
    assert r["ai_faster_pct"] == 73.5          # 90 / 122.5


def test_invalid_and_missing_values_are_dropped():
    ai = [{"cycle_seconds": s} for s in (10, 20, 30, 40, "bogus", None, -5, 50)]
    manual = [{"cycle_seconds": s} for s in (0, 100, 110, 120, 130, 140)]
    r = ab_report(ai, manual)
    assert r["ai_assisted"]["count"] == 5      # bogus/None/negative dropped
    assert r["manual"]["count"] == 5           # zero dropped
    assert r["sample_sufficient"] is True


def test_ticket_cycle_seconds_flows_into_the_run(client):
    t = {"customer_email": "a@b.com", "subject": "Charged twice",
         "body": "You charged my card twice for $49, please refund one.",
         "cycle_seconds": 42.0}
    assert client.post("/api/tickets", json=t).status_code == 200
    run = client.get("/api/runs").json()[-1]
    assert run["cycle_seconds"] == 42.0
    body = client.get("/api/analytics/ab").json()
    assert body["ai_assisted"]["count"] == 1


def test_manual_record_endpoint_and_report(client):
    r = client.post("/api/analytics/ab/records",
                    json={"ticket_id": "tkt_1", "label": "duplicate charge",
                          "cycle_seconds": 300.0})
    assert r.status_code == 201
    assert r.json()["cohort"] == "manual"
    body = client.get("/api/analytics/ab").json()
    assert body["manual"]["count"] == 1
    assert body["manual"]["median_seconds"] == 300.0


def test_manual_record_rejects_non_positive(client):
    r = client.post("/api/analytics/ab/records", json={"cycle_seconds": 0})
    assert r.status_code == 422
