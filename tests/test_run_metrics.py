"""Tests for run performance metrics (percentiles, failure, containment)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.run_metrics import percentile, run_performance_metrics  # noqa: E402


def test_percentile_matches_linear_interpolation():
    vals = [10, 20, 30, 40]
    assert percentile(vals, 50) == 25.0
    assert percentile(vals, 0) == 10.0
    assert percentile(vals, 100) == 40.0
    assert percentile([], 95) == 0.0
    assert percentile([5], 95) == 5.0


def _run(disp: str, ms: float, cost: float) -> dict:
    return {"disposition": disp, "total_latency_ms": ms, "total_cost_usd": cost}


def test_metrics_math():
    runs = [
        _run("auto_resolved", 100, 0.0002),
        _run("auto_resolved", 200, 0.0003),
        _run("human_review", 300, 0.0004),
        _run("approved_executed", 400, 0.0005),
        _run("failed", 50, 0.0001),
    ]
    m = run_performance_metrics(runs)
    assert m["runs"] == 5
    assert m["latency_ms"]["p50"] == 200.0
    assert m["latency_ms"]["p95"] == 380.0
    assert m["latency_ms"]["max"] == 400.0
    assert m["failure_rate_pct"] == 20.0
    assert m["containment_rate_pct"] == 40.0   # 2 auto / 5
    assert m["human_touch_rate_pct"] == 40.0   # human_review + approved_executed
    assert m["cost_per_run_usd"]["mean"] == 0.0003


def test_zero_state():
    m = run_performance_metrics([])
    assert m["runs"] == 0
    assert m["latency_ms"]["p95"] == 0.0
    assert m["failure_rate_pct"] == 0.0


def test_endpoint_on_live_data(client):
    """Seeded, deterministic record — CI machines run the mock pipeline in
    under 0.05 ms, so asserting on measured wall-clock latency is flaky."""
    from backend.models import PipelineResult
    from backend.store import storage

    rec = PipelineResult(ticket_id="t_ci", disposition="auto_resolved",
                         total_cost_usd=0.0002, total_latency_ms=12.5).model_dump()
    storage.append("runs", rec)
    m = client.get("/api/analytics/runs").json()
    assert m["runs"] == 1
    assert m["containment_rate_pct"] == 100.0
    assert m["failure_rate_pct"] == 0.0
    assert m["latency_ms"]["p50"] == 12.5
    assert m["latency_ms"]["max"] == 12.5
    assert m["cost_per_run_usd"]["mean"] == 0.0002


def test_sub_millisecond_latency_is_preserved():
    """Regression pin: rounding must not collapse fast runs to 0.0 ms
    (this exact collapse broke the 3.11 CI job)."""
    m = run_performance_metrics([_run("auto_resolved", 0.012, 0.0002)])
    assert m["latency_ms"]["p50"] == 0.012
    assert m["latency_ms"]["p95"] == 0.012
