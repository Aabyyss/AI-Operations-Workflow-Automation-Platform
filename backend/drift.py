"""Drift canary — did recent behavior quietly diverge from the baseline?

Compares a recent window of runs against the preceding baseline window
on the three signals that matter operationally: containment (automation
rate), escalation rate, and failure rate. A sustained shift in any of
them usually means something changed upstream — a prompt edit, a new
ticket mix, a policy document rewrite — long before anyone files a bug.

Thresholds are deltas in percentage points, deliberately coarse: this is
a smoke alarm, not a statistics engine.
"""
from __future__ import annotations

AUTO = {"auto_resolved", "auto_replied"}
HUMAN = {"human_review", "approved_executed", "rejected"}
FAILED = {"failed"}

ALERT_THRESHOLD_PCT = 10.0  # percentage-point swing that pages someone


def _rate(records: list[dict], group: set[str]) -> float:
    if not records:
        return 0.0
    return 100.0 * sum(1 for r in records if r.get("disposition") in group) / len(records)


def drift_report(runs: list[dict], window: int = 50,
                 threshold: float = ALERT_THRESHOLD_PCT) -> dict:
    """Compare the last `window` runs against the `window` before them."""
    total = len(runs)
    if total < window * 2:
        return {
            "status": "insufficient_data",
            "runs_total": total,
            "required": window * 2,
            "signals": {},
        }

    baseline, recent = runs[-(window * 2):-window], runs[-window:]
    signals = {}
    for name, group in (("containment", AUTO), ("escalation", HUMAN), ("failure", FAILED)):
        base_pct = round(_rate(baseline, group), 1)
        rec_pct = round(_rate(recent, group), 1)
        delta = round(rec_pct - base_pct, 1)
        signals[name] = {
            "baseline_pct": base_pct,
            "recent_pct": rec_pct,
            "delta_pct": delta,
            "alert": abs(delta) > threshold,
        }

    return {
        "status": "alert" if any(s["alert"] for s in signals.values()) else "nominal",
        "window": window,
        "runs_total": total,
        "threshold_pct": threshold,
        "signals": signals,
    }
