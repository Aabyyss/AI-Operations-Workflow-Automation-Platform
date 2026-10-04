"""Quality-gate quality: how often is the gate right?

Every escalation is an experiment with ground truth attached. When the
reviewer APPROVES, the gate was over-cautious (false positive — a ticket
that could have auto-resolved got held). When the reviewer REJECTS, the
gate earned its keep (true positive — a bad draft never shipped). This
module turns the review ledger into precision numbers, and uses
thumbs-down feedback on auto-resolved runs as a proxy for the gate's
false negatives — the escalations that should have happened but didn't.

All of it is measured on decisions that already happened: no re-scoring,
no model access, works offline in mock mode.
"""
from __future__ import annotations


def gate_quality(reviews: list[dict], runs: list[dict], feedback: list[dict]) -> dict:
    decided = [r for r in reviews if r.get("status") in ("approved", "rejected")]
    rejected = sum(1 for r in decided if r.get("status") == "rejected")
    approved = len(decided) - rejected
    pending = sum(1 for r in reviews if r.get("status") == "pending")

    # Precision: of the escalations a human judged, what share were real catches?
    precision = round(rejected / len(decided), 3) if decided else None

    # Recall proxy: auto-resolved runs later thumbed-down by the operator
    # are, with hindsight, escalations the gate should have made.
    auto_runs = [r for r in runs if r.get("disposition") == "auto_resolved"]
    down_run_ids = {f.get("run_id") for f in feedback if f.get("rating") == "down"}
    flagged = [r for r in auto_runs if r.get("id") in down_run_ids]
    recall_proxy = round(len(flagged) / len(auto_runs), 3) if auto_runs else None

    return {
        "escalations_decided": len(decided),
        "escalations_pending": pending,
        "reviewer_approved": approved,   # gate over-fired
        "reviewer_rejected": rejected,   # gate caught a real problem
        "gate_precision": precision,
        "auto_resolved_total": len(auto_runs),
        "flagged_by_feedback": len(flagged),
        "gate_recall_proxy": recall_proxy,
        "note": ("precision = rejected escalations / decided escalations; "
                 "recall proxy = thumbs-down auto-resolutions / auto-resolutions"),
    }
