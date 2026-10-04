"""A/B cycle-time tracking: is the AI actually faster than the manual path?

The ROI engine estimates savings from assumptions; this module measures the
claim. Tickets are tagged with a cohort — `ai_assisted` (handled through the
pipeline) or `manual` (handled by a human without it) — and each cohort gets
a cycle-time distribution. The report is deliberately honest about sample
size: with fewer than 5 records in either cohort it says so instead of
publishing a "% faster" number built on two data points.

Ingestion path: n8n (or the dashboard) stamps cycle_seconds on a ticket's
run record — `POST /api/tickets` accepts an optional cycle_seconds field for
ai_assisted timing, and POST /api/analytics/ab records the manual cohort.
"""
from __future__ import annotations

MIN_COHORT_SIZE = 5

COHORTS = ("ai_assisted", "manual")


def _clean_seconds(values: list) -> list[float]:
    out = []
    for v in values:
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if f > 0:
            out.append(f)
    return out


def _distribution(values: list[float]) -> dict:
    if not values:
        return {"count": 0, "median_seconds": None, "p90_seconds": None, "mean_seconds": None}
    s = sorted(values)
    n = len(s)

    def pct(p: float) -> float:
        """Linear interpolation, same convention as run_metrics."""
        k = (n - 1) * p
        lo = int(k)
        hi = min(lo + 1, n - 1)
        return round(s[lo] + (s[hi] - s[lo]) * (k - lo), 1)

    return {"count": n, "median_seconds": pct(0.5), "p90_seconds": pct(0.9),
            "mean_seconds": round(sum(s) / n, 1)}


def ab_report(runs: list[dict], manual_records: list[dict]) -> dict:
    """Cycle-time comparison between the AI-assisted and manual cohorts.

    runs: pipeline run records (ai_assisted cohort; cycle_seconds optional).
    manual_records: dicts with at least {cycle_seconds} stamped by whoever
    tracked the manual process.
    """
    ai_values = _clean_seconds([r.get("cycle_seconds") for r in runs])
    manual_values = _clean_seconds([m.get("cycle_seconds") for m in manual_records])

    ai = _distribution(ai_values)
    manual = _distribution(manual_values)

    enough = ai["count"] >= MIN_COHORT_SIZE and manual["count"] >= MIN_COHORT_SIZE
    delta = None
    faster_pct = None
    if enough and manual["median_seconds"]:
        delta = round(manual["median_seconds"] - ai["median_seconds"], 1)
        faster_pct = round(100.0 * delta / manual["median_seconds"], 1)

    return {
        "ai_assisted": ai,
        "manual": manual,
        "median_delta_seconds": delta,
        "ai_faster_pct": faster_pct,
        "sample_sufficient": enough,
        "min_cohort_size": MIN_COHORT_SIZE,
    }
