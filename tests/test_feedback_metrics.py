"""Tests for the feedback analytics join."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.run_metrics import feedback_metrics  # noqa: E402


def test_empty_state_is_zeroed():
    m = feedback_metrics([], [])
    assert m == {
        "feedback_count": 0, "thumbs_up": 0, "thumbs_down": 0,
        "satisfaction_pct": 0.0, "with_correction": 0, "coverage_pct": 0.0,
    }


def test_ratio_and_coverage():
    fb = [
        {"rating": "up", "correction": None},
        {"rating": "up", "correction": "should have escalated"},
        {"rating": "down", "correction": None},
    ]
    m = feedback_metrics(fb, [{"id": f"run_{i}"} for i in range(10)])
    assert m["feedback_count"] == 3
    assert m["thumbs_up"] == 2 and m["thumbs_down"] == 1
    assert m["satisfaction_pct"] == 66.7
    assert m["with_correction"] == 1
    assert m["coverage_pct"] == 30.0
