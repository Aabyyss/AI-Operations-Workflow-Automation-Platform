"""Run-level performance metrics — how well the pipeline is actually running.

Latency percentiles, failure rate, containment (share of tickets resolved
without a human touching them), human-touch rate, and cost per run. This
is the LLMOps slice of the monitoring story: quality gates prove *correct*
routing, these numbers prove the pipeline stays fast, cheap, and mostly
autonomous as volume grows.

Pure functions over stored run records; no clock needed.
"""
from __future__ import annotations

import math

AUTO_DISPOSITIONS = {"auto_resolved", "auto_replied"}
HUMAN_DISPOSITIONS = {"human_review", "approved_executed", "rejected"}


def percentile(values: list[float], p: float) -> float:
    """Linear-interpolation percentile (numpy 'linear' method), 0 <= p <= 100."""
    if not values:
        return 0.0
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    rank = (p / 100.0) * (len(s) - 1)
    lo = math.floor(rank)
    hi = math.ceil(rank)
    if lo == hi:
        return s[lo]
    frac = rank - lo
    return s[lo] + (s[hi] - s[lo]) * frac


def run_performance_metrics(runs: list[dict]) -> dict:
    """Compute latency, failure, containment, and cost metrics from run records."""
    total = len(runs)
    latencies = [float(r["total_latency_ms"]) for r in runs
                 if r.get("total_latency_ms") is not None]
    costs = [float(r["total_cost_usd"]) for r in runs
             if r.get("total_cost_usd") is not None]
    failures = sum(1 for r in runs if r.get("disposition") == "failed")
    auto = sum(1 for r in runs if r.get("disposition") in AUTO_DISPOSITIONS)
    human = sum(1 for r in runs if r.get("disposition") in HUMAN_DISPOSITIONS)

    return {
        "runs": total,
        "latency_ms": {
            "p50": round(percentile(latencies, 50), 1),
            "p95": round(percentile(latencies, 95), 1),
            "max": round(max(latencies), 1) if latencies else 0.0,
            "mean": round(sum(latencies) / len(latencies), 1) if latencies else 0.0,
        },
        "failure_rate_pct": round(100.0 * failures / max(1, total), 1),
        "containment_rate_pct": round(100.0 * auto / max(1, total), 1),
        "human_touch_rate_pct": round(100.0 * human / max(1, total), 1),
        "cost_per_run_usd": {
            "mean": round(sum(costs) / len(costs), 5) if costs else 0.0,
            "p95": round(percentile(costs, 95), 5) if costs else 0.0,
        },
    }
