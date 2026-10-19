"""Shadow mode — score live traffic through the risk gates without acting.

The question an operator asks before trusting an agent with real work is
"what would it have done?", and the only honest way to answer it is to run
the real thing on real tickets with every side effect switched off.

So shadow scoring calls the *same* intake, retrieval and decision functions
as a live run — not a copy, not a reimplementation that drifts. What it
skips is everything after the decision: no draft, no email, no refund, no
review request, and no entry in the run ledger. A shadow run therefore
cannot move containment, escalation or failure rates, which is the property
that makes it safe to leave switched on during an evaluation.

What it does record is the decision it would have made: the risk score, the
triggers behind it, the actions it would have taken, the gates in force and
the policy documents it retrieved. `report()` aggregates that into the one
number the evaluation turns on — how many tickets it would have escalated —
with the triggers split out, because "22% escalated" is not actionable and
"14 of 18 escalations were refunds over the limit" is.

Personal data stays out: the record keeps the ticket id, category and
priority, never the body or the address.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from . import config, store
from .integrations.audit import audit_log
from .models import Disposition, PipelineResult, Ticket, iso_now, new_id
from .pipeline import run_decision, run_intake, run_knowledge
from .receipts import gates_snapshot

SHADOW_VERSION = 1


def build_record(ticket: Ticket, intake, knowledge, decision, *,
                 result_id: str | None = None,
                 cost_usd: float = 0.0) -> dict:
    """The shadow ledger row: what the gate would have done, and why."""
    triggers = [t.strip() for t in (decision.reason or "").split(";") if t.strip()]
    return {
        "id": new_id("shd"),
        "created_at": iso_now(),
        "shadow_version": SHADOW_VERSION,
        "result_id": result_id,
        "ticket": {
            "id": ticket.id,
            "category": intake.category.value,
            "priority": intake.priority.value,
            "requested_refund_usd": intake.requested_refund_usd,
            "has_customer_email": bool(ticket.customer_email),
        },
        "would_escalate": decision.needs_human_approval,
        "would_auto_resolve": decision.can_auto_resolve,
        "risk_score": decision.risk_score,
        "confidence": decision.confidence,
        "reason": decision.reason,
        "triggers": triggers,
        "proposed_actions": decision.proposed_actions,
        "retrieval": [{"doc_id": c.doc_id, "score": c.score}
                      for c in (knowledge.chunks or [])],
        "gates": gates_snapshot(),
        "mode": config.MODE,
        "cost_usd": cost_usd,
    }


def score(ticket: Ticket) -> dict:
    """Score one ticket end-to-end up to the decision, and record it."""
    trace: list = []
    usage: list = []
    intake = run_intake(ticket, trace, usage)
    knowledge = run_knowledge(intake, ticket, trace)
    decision = run_decision(intake, knowledge, ticket, trace)
    record = build_record(ticket, intake, knowledge, decision,
                          cost_usd=round(sum(u.cost_usd for u in usage), 6))
    store.append("shadow", record)
    audit_log("shadow_scored", {"ticket_id": ticket.id,
                                "would_escalate": record["would_escalate"],
                                "risk": record["risk_score"]})
    return record


def score_result(ticket: Ticket) -> PipelineResult:
    """The same scoring, shaped as an API response.

    Deliberately a distinct disposition: a shadow run must not be countable
    as a containment success or a human escalation anywhere downstream.
    """
    trace: list = []
    usage: list = []
    result = PipelineResult(ticket_id=ticket.id, disposition=Disposition.SHADOW_SCORED)
    result.intake = run_intake(ticket, trace, usage)
    result.knowledge = run_knowledge(result.intake, ticket, trace)
    result.decision = run_decision(result.intake, result.knowledge, ticket, trace)
    result.trace = trace
    result.total_cost_usd = round(sum(u.cost_usd for u in usage), 6)
    result.total_latency_ms = round(sum(t.duration_ms for t in trace), 3)
    result.cycle_seconds = ticket.cycle_seconds

    record = build_record(ticket, result.intake, result.knowledge, result.decision,
                          result_id=result.id, cost_usd=result.total_cost_usd)
    store.append("shadow", record)
    audit_log("shadow_scored", {"ticket_id": ticket.id,
                                "would_escalate": record["would_escalate"],
                                "risk": record["risk_score"]})
    return result


def _percentage(part: int, whole: int) -> float:
    return round(100.0 * part / whole, 1) if whole else 0.0


def report(records: list[dict] | None = None) -> dict:
    """How many tickets shadow mode would have escalated, and on what."""
    rows = store.all("shadow") if records is None else records
    total = len(rows)
    escalated = sum(1 for r in rows if r.get("would_escalate"))
    triggers: Counter = Counter()
    for row in rows:
        if row.get("would_escalate"):
            for trigger in row.get("triggers") or []:
                triggers[trigger] += 1
    by_category: Counter = Counter()
    actions: Counter = Counter()
    for row in rows:
        by_category[(row.get("ticket") or {}).get("category", "unknown")] += 1
        for action in row.get("proposed_actions") or []:
            actions[action.split(":", 1)[0]] += 1

    return {
        "shadow_version": SHADOW_VERSION,
        "count": total,
        "would_have_escalated": escalated,
        "would_have_auto_resolved": total - escalated,
        "escalation_rate_pct": _percentage(escalated, total),
        "top_triggers": [{"trigger": t, "tickets": n}
                         for t, n in triggers.most_common(8)],
        "by_category": dict(by_category.most_common()),
        "actions_that_would_have_run": dict(actions.most_common()),
        "total_cost_usd": round(sum(float(r.get("cost_usd") or 0) for r in rows), 6),
        "mode": config.MODE,
        "live_traffic_is_shadowed": config.SHADOW_MODE,
        "gates": gates_snapshot(),
        "sample_note": ("No tickets scored yet." if not total else
                        f"{total} ticket(s) scored; the gate would have acted on "
                        f"{escalated} of them."),
        "what_it_does_not_do": ("Shadow scoring never drafts, sends, refunds or "
                                "creates a review, and never writes to the run "
                                "ledger — containment and escalation rates are "
                                "untouched by design."),
    }


def clear() -> int:
    """Drop the shadow ledger (a new evaluation starts from an empty one)."""
    removed = len(store.all("shadow"))
    store.storage.replace_all("shadow", [])
    audit_log("shadow_cleared", {"records": removed})
    return removed
