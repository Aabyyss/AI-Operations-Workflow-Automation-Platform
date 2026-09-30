"""Tests for budget guardrails."""
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.budget import budget_status  # noqa: E402

MID_MONTH = datetime(2026, 10, 15, tzinfo=timezone.utc)


def _usage(*costs):
    return [{"cost_usd": c} for c in costs]


def test_disabled_without_budget():
    m = budget_status(_usage(0.01, 0.02), budget_usd=0.0, now=MID_MONTH)
    assert m["enabled"] is False and m["alert"] is None
    assert m["spent_usd"] == 0.03


def test_spend_and_projection():
    # $0.30 spent by day 15 → projected $0.60 for the month.
    m = budget_status(_usage(0.2, 0.1), budget_usd=1.0, now=MID_MONTH)
    assert m["spent_usd"] == 0.3
    assert m["remaining_usd"] == 0.7
    assert m["utilization_pct"] == 30.0
    assert m["projected_month_usd"] == 0.6
    assert m["alert"] is False


def test_alert_on_projection_overrun():
    # Spending the whole budget in half a month → 200% projection.
    m = budget_status(_usage(0.5, 0.5), budget_usd=1.0, now=MID_MONTH)
    assert m["alert"] is True


def test_alert_on_absolute_overspend():
    m = budget_status(_usage(1.4), budget_usd=1.0, now=MID_MONTH)
    assert m["alert"] is True and m["remaining_usd"] < 0
