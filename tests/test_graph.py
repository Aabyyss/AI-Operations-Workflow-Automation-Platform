"""Tests for the LangGraph orchestration — backend/graph.py.

pipeline.py is the source of truth for behavior; graph.py re-expresses the
same control flow as an inspectable graph (intake -> knowledge -> decision
-> auto/human). Until now nothing exercised it, so a refactor of either
side could desynchronize the two orchestrations silently — the exact drift
the docstring promises this graph makes visible.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import graph, pipeline  # noqa: E402
from backend.models import Ticket  # noqa: E402


def _ticket(email: str = "jordan.miles@northwind.example",
            subject: str = "Charged twice for my subscription",
            body: str = "Charged twice - $49.00 on the 3rd and again on "
                        "the 5th. Please refund one of the charges.") -> Ticket:
    return Ticket(customer_email=email, subject=subject, body=body)


def test_auto_path_executes():
    """A low-risk, well-grounded ticket runs end-to-end in the graph."""
    out = graph.run_via_graph(_ticket())
    assert out["route"] == "executed"
    assert out["decision"]["can_auto_resolve"] is True
    assert out["decision"]["needs_human_approval"] is False
    assert out["result"]["response"]          # a draft was produced
    assert out["result"]["actions"]           # and executed


def test_high_value_refund_routed_to_human_review():
    """Above the monetary approval limit the graph escalates instead."""
    out = graph.run_via_graph(_ticket(
        email="big@corp.example",
        subject="Refund for annual plan",
        body="I was charged $1,200.00 and need a full refund now."))
    assert out["route"] == "human_review"
    assert out["decision"]["needs_human_approval"] is True
    assert out["result"]["review_id"]          # a review was queued
    # The human branch drafts before escalating (as run_pipeline does):
    # the queued review carries a proposed response, not an empty string.
    from backend.store import storage
    review = storage.get("reviews", out["result"]["review_id"])
    assert review is not None
    assert review["proposed_response"].strip()


def test_graph_and_plain_pipeline_agree_on_routing():
    """Same question, both orchestrations: the graph's route and the plain
    pipeline's disposition must tell the same story for the same inputs.

    This is the drift the graph exists to make visible — if a pipeline
    change flips one without the other, this fails first.
    """
    auto_graph = graph.run_via_graph(_ticket(email="auto@graph.example"))
    auto_plain = pipeline.run_pipeline(_ticket(email="auto@plain.example"))
    assert auto_graph["route"] == "executed"
    assert auto_plain.disposition.value in ("auto_resolved", "auto_replied")

    human_graph = graph.run_via_graph(_ticket(
        email="big@graph.example", subject="Refund for annual plan",
        body="I was charged $1,200.00 and need a full refund now."))
    human_plain = pipeline.run_pipeline(_ticket(
        email="big@plain.example", subject="Refund for annual plan",
        body="I was charged $1,200.00 and need a full refund now."))
    assert human_graph["route"] == "human_review"
    assert human_plain.disposition.value == "human_review"


def test_graph_records_every_state_stage():
    """The state dict carries each stage an auditor would want to inspect."""
    out = graph.run_via_graph(_ticket(email="trace@graph.example"))
    for stage in ("intake", "knowledge", "decision", "draft", "quality",
                  "result"):
        assert stage in out, f"graph state missing stage: {stage}"
    assert out["ticket_obj"] is not None       # the parsed model survives
    assert out["route"] in ("executed", "human_review")
