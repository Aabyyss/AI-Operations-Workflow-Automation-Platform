"""Smoke test for the end-to-end demo (scripts/demo.py).

`python -m scripts.demo` is the first thing a reviewer runs: Side A
analysis, a batch of Side B tickets, the approval round-trip, the learning
loop, signed receipts, shadow mode and the monitoring summary. Nothing
exercised it, so a break anywhere in that chain would ship unnoticed.

The conftest fixtures point DATA_DIR at a temp dir, so the demo's own
reset_data() — which rmtree's DATA_DIR for reproducible numbers — only ever
wipes test temp data.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import demo  # noqa: E402


def test_demo_runs_end_to_end(capsys):
    demo.reset_data()
    demo.side_a()
    demo.side_b()
    demo.approve_one()
    demo.learning_loop()
    demo.receipts_and_shadow()
    demo.summary()
    out = capsys.readouterr().out

    # Side A: analysis with a score and the n8n bridge.
    assert "SIDE A" in out
    assert "Automation score" in out
    assert "Generated n8n workflow" in out

    # Side B: the batch ran and every result has a disposition.
    assert "SIDE B" in out
    assert "Batch summary" in out
    assert "disposition=" in out

    # The approval round-trip completed (not "No pending reviews").
    assert "Decision: approved by manager_amy" in out

    # Learning loop: feedback landed, drift/budget/metrics rendered.
    assert "Feedback recorded" in out
    assert "Prometheus /metrics: HTTP 200" in out

    # Proof: a receipt verified in-process and shadow scored tickets.
    assert "verified in-process: True" in out
    assert "Shadow mode scored 2 ticket(s)" in out

    # Monitoring summary rendered with real numbers.
    assert "Tickets processed" in out
    assert "Automation rate" in out

    # No stage printed an error.
    assert "ERROR" not in out
