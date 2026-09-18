"""The agentic support pipeline.

Six agents behind typed contracts:
  1. IntakeAgent     — classify, prioritize, extract entities.
  2. KnowledgeAgent  — RAG over the company knowledge base.
  3. DecisionAgent   — risk evaluation: auto / human-approval / reject.
  4. DraftAgent      — policy-grounded customer response.
  5. QualityAgent    — groundedness, tone, PII checks before anything ships.
  6. ActionAgent     — executes approved actions via integrations.
  7. EscalationAgent — hands off to a human review queue.

Governance is code, not vibes: money/PII gates and confidence thresholds are
enforced deterministically in DecisionAgent, regardless of what any model says.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

from . import config, store
from .integrations import audit_log
from .llm import get_llm, record_usage
from .models import (
    AgentTrace, DecisionResult, Disposition, DraftResponse, IntakeResult,
    KnowledgeResult, PipelineResult, Priority, QualityResult, ReviewRequest,
    Ticket, TicketCategory, iso_now, new_id,
)
from .rag import retriever

AMOUNT_RE = re.compile(r"\$\s?([\d,]+(?:\.\d+)?)")


def _trace(agent: str, t0: float, inp: str, out: str, usage: Any = None) -> AgentTrace:
    tr = AgentTrace(agent=agent, started_at=t0, input_summary=inp[:160], output_summary=out[:160])
    if usage is not None:
        tr.tokens_in, tr.tokens_out = usage.tokens_in, usage.tokens_out
        tr.cost_usd, tr.mode = usage.cost_usd, usage.mode
    tr.duration_ms = int((time.time() - t0) * 1000)
    return tr


# ---------------------------------------------------------------------------
# Agent 1 — Intake
# ---------------------------------------------------------------------------
def run_intake(ticket: Ticket, trace: list[AgentTrace], usage_log: list) -> IntakeResult:
    t0 = time.time()
    llm = get_llm()
    prompt = (
        f"TICKET_SUBJECT:{ticket.subject}\nTICKET_BODY:{ticket.body}\n"
        'MOCK_TASK:{"task":"intake"}'
    )
    resp = llm.chat(
        system="Classify the support ticket. Return JSON with category, priority, intent, entities, requested_refund_usd, confidence.",
        user=prompt,
    )
    data = json.loads(resp["text"])
    usage = record_usage(llm, resp["model"], resp["tokens_in"], resp["tokens_out"], "intake", ticket.id)
    usage_log.append(usage)
    trace.append(_trace("intake", t0, f"{ticket.subject} / {ticket.body[:80]}", json.dumps(data), usage))
    return IntakeResult(
        category=TicketCategory(data["category"]),
        priority=Priority(data["priority"]),
        intent=data["intent"],
        entities=data.get("entities", {}),
        requested_refund_usd=float(data.get("requested_refund_usd", 0) or 0),
        confidence=float(data.get("confidence", 0.5)),
    )


# ---------------------------------------------------------------------------
# Agent 2 — Knowledge (RAG)
# ---------------------------------------------------------------------------
def run_knowledge(intake: IntakeResult, ticket: Ticket, trace: list[AgentTrace]) -> KnowledgeResult:
    t0 = time.time()
    query = f"{ticket.subject} {ticket.body[:300]}"
    chunks = retriever.retrieve(query, k=4)
    good = [c for c in chunks if c.score >= config.MIN_RETRIEVAL_SCORE]
    # Category-aware fallback so an odd phrasing still finds the right policy.
    if not good:
        cat_queries = {
            TicketCategory.BILLING: "refund duplicate charge policy",
            TicketCategory.ACCOUNT: "password reset locked account",
            TicketCategory.TECHNICAL: "api errors troubleshooting",
            TicketCategory.SALES: "pricing plans trial discount",
        }
        good = [c for c in retriever.retrieve(cat_queries.get(intake.category, "support policy"), k=3)
                if c.score >= config.MIN_RETRIEVAL_SCORE]
    summary = " ".join(c.text[:220] for c in good[:2])
    conf = min(1.0, (good[0].score * 2.2) if good else 0.0)
    trace.append(_trace("knowledge", t0, query, f"{len(good)} chunks, top={good[0].title if good else 'none'}"))
    return KnowledgeResult(
        chunks=good,
        policy_summary=summary,
        sufficient=bool(good),
        confidence=round(conf, 3),
    )


# ---------------------------------------------------------------------------
# Agent 3 — Decision (risk engine; deterministic, auditable)
# ---------------------------------------------------------------------------
def run_decision(intake: IntakeResult, knowledge: KnowledgeResult, ticket: Ticket,
                 trace: list[AgentTrace]) -> DecisionResult:
    t0 = time.time()
    risk = 0.05
    reasons: list[str] = []
    actions: list[str] = []

    if intake.category == TicketCategory.BILLING and intake.requested_refund_usd > 0:
        actions.append(f"queue_refund:${intake.requested_refund_usd:.2f}")
        if intake.requested_refund_usd >= config.MONETARY_APPROVAL_LIMIT_USD:
            risk += 0.60
            reasons.append(f"refund ${intake.requested_refund_usd:.2f} >= ${config.MONETARY_APPROVAL_LIMIT_USD:.0f} limit")
        else:
            risk += 0.10
            reasons.append("small refund, within auto-approve policy")
    if intake.category == TicketCategory.BILLING:
        actions.append("send_reply")
    if intake.category in (TicketCategory.TECHNICAL,):
        actions.append("send_troubleshooting_steps")
        risk += 0.10
    if intake.category == TicketCategory.ACCOUNT:
        actions.append("send_reset_guidance")
        risk += 0.05
        account_text = (ticket.subject + " " + ticket.body).lower()
        if "2fa" in account_text or "two-factor" in account_text or "two factor" in account_text:
            risk += 0.55
            reasons.append("2FA removal is a security-controlled action")
    if intake.category == TicketCategory.SALES:
        actions.append("send_pricing_info")
        risk += 0.02
    if intake.category == TicketCategory.OTHER or not knowledge.sufficient:
        risk += 0.45
        reasons.append("no confident policy match — human needed")

    # Confidence gates: the model may be sure, but governance decides.
    if intake.confidence < 0.6:
        risk += 0.25
        reasons.append(f"low intake confidence ({intake.confidence:.2f})")
    if knowledge.confidence < 0.3:
        risk += 0.20
        reasons.append(f"weak retrieval confidence ({knowledge.confidence:.2f})")

    if intake.priority == Priority.URGENT:
        reasons.append("urgent priority flagged for human awareness")
        risk += 0.05

    risk = min(1.0, round(risk, 3))
    can_auto = risk <= config.AUTO_EXECUTE_MAX_RISK and knowledge.sufficient
    needs_human = not can_auto
    if risk > config.BLOCK_MIN_RISK:
        can_auto, needs_human = False, True
        reasons.append("risk above hard block threshold")

    result = DecisionResult(
        can_auto_resolve=can_auto,
        needs_human_approval=needs_human,
        reason="; ".join(reasons) or "low risk, policy-grounded",
        risk_score=risk,
        confidence=round((intake.confidence + knowledge.confidence) / 2, 3),
        proposed_actions=actions or ["send_reply"],
    )
    trace.append(_trace("decision", t0, f"risk inputs: {len(reasons)} factors", json.dumps(result.model_dump())))
    return result


# ---------------------------------------------------------------------------
# Agent 4 — Draft
# ---------------------------------------------------------------------------
def run_draft(ticket: Ticket, intake: IntakeResult, knowledge: KnowledgeResult,
              trace: list[AgentTrace], usage_log: list) -> DraftResponse:
    t0 = time.time()
    llm = get_llm()
    policy = knowledge.chunks[0].text if knowledge.chunks else ""
    prompt = (
        f"TICKET_SUBJECT:{ticket.subject}\n"
        f"TICKET_BODY:{ticket.body[:600]}\n"
        f"CATEGORY:{intake.category.value}\n"
        f"CUSTOMER_EMAIL:{ticket.customer_email}\n"
        f"REFUND_USD:{intake.requested_refund_usd}\n"
        f"POLICY_SNIPPET:{policy}\n"
        'COMPOSE_NOW MOCK_TASK:{"task":"draft"}'
    )
    resp = llm.chat(
        system="Write a short, warm, policy-grounded customer reply. Only promise what the policy says.",
        user=prompt,
    )
    data = json.loads(resp["text"])
    usage = record_usage(llm, resp["model"], resp["tokens_in"], resp["tokens_out"], "draft", ticket.id)
    usage_log.append(usage)
    grounded_in = [c.doc_id for c in knowledge.chunks[:3]]
    trace.append(_trace("draft", t0, f"grounded in {grounded_in}", f"{len(data['text'])} chars", usage))
    return DraftResponse(text=data["text"], grounded_in=grounded_in)


# ---------------------------------------------------------------------------
# Agent 5 — Quality gate
# ---------------------------------------------------------------------------
def run_quality(draft: DraftResponse, knowledge: KnowledgeResult, ticket: Ticket,
                trace: list[AgentTrace], usage_log: list) -> QualityResult:
    t0 = time.time()
    llm = get_llm()
    grounded = bool(draft.grounded_in) and knowledge.sufficient
    prompt = (
        f"DRAFT_TEXT:{draft.text}\n"
        f"GROUNDING_OK:{'yes' if grounded else 'no'}\n"
        'MOCK_TASK:{"task":"quality"}'
    )
    resp = llm.chat(
        system="Check the draft for groundedness, tone, and PII leaks. Return JSON.",
        user=prompt,
    )
    data = json.loads(resp["text"])
    usage = record_usage(llm, resp["model"], resp["tokens_in"], resp["tokens_out"], "quality", ticket.id)
    usage_log.append(usage)
    result = QualityResult(
        passed=bool(data["passed"]),
        grounded=bool(data["grounded"]),
        tone_ok=bool(data["tone_ok"]),
        pii_leak=bool(data["pii_leak"]),
        issues=data.get("issues", []),
        confidence=float(data.get("confidence", 0.5)),
    )
    trace.append(_trace("quality", t0, "gates: grounded/tone/pii", json.dumps(result.model_dump()), usage))
    return result


# ---------------------------------------------------------------------------
# Agent 6 — Actions
# ---------------------------------------------------------------------------
def run_actions(ticket: Ticket, intake: IntakeResult, decision: DecisionResult,
                response_text: str, trace: list[AgentTrace]) -> list[str]:
    from .integrations import actions as integ
    t0 = time.time()
    taken: list[str] = []
    for action in decision.proposed_actions:
        if action.startswith("queue_refund:"):
            amount = float(action.split(":", 1)[1].replace("$", "").replace(",", ""))
            taken.append(integ.queue_refund(ticket, intake, amount))
        elif action == "send_reset_guidance":
            taken.append(integ.send_email(ticket, response_text))
        elif action == "send_troubleshooting_steps":
            taken.append(integ.send_email(ticket, response_text))
        elif action == "send_pricing_info":
            taken.append(integ.send_email(ticket, response_text))
        elif action == "send_reply":
            taken.append(integ.send_email(ticket, response_text))
    taken.append(integ.update_crm(ticket, intake, response_text))
    audit_log("actions_executed", {"ticket_id": ticket.id, "actions": taken})
    trace.append(_trace("actions", t0, json.dumps(decision.proposed_actions), json.dumps(taken)))
    return taken


# ---------------------------------------------------------------------------
# Agent 7 — Escalation
# ---------------------------------------------------------------------------
def run_escalation(ticket: Ticket, decision: DecisionResult, draft: DraftResponse,
                   trace: list[AgentTrace]) -> ReviewRequest:
    t0 = time.time()
    review = ReviewRequest(
        ticket_id=ticket.id,
        risk_score=decision.risk_score,
        reason=decision.reason,
        proposed_response=draft.text,
        proposed_actions=decision.proposed_actions,
    )
    store.append("reviews", review.model_dump())
    audit_log("escalated_to_human", {"ticket_id": ticket.id, "review_id": review.id,
                                     "risk": decision.risk_score, "reason": decision.reason})
    trace.append(_trace("escalation", t0, f"risk={decision.risk_score}", review.id))
    return review


# ---------------------------------------------------------------------------
# Orchestration — one function, also exposed to LangGraph in graph.py
# ---------------------------------------------------------------------------
def run_pipeline(ticket: Ticket) -> PipelineResult:
    trace: list[AgentTrace] = []
    usage_log: list = []
    result = PipelineResult(ticket_id=ticket.id, disposition=Disposition.FAILED)

    store.append("tickets", ticket.model_dump())
    try:
        result.intake = run_intake(ticket, trace, usage_log)
        result.knowledge = run_knowledge(result.intake, ticket, trace)
        result.decision = run_decision(result.intake, result.knowledge, ticket, trace)

        if not result.decision.can_auto_resolve:
            result.draft = run_draft(ticket, result.intake, result.knowledge, trace, usage_log)
            review = run_escalation(ticket, result.decision, result.draft, trace)
            result.review_id = review.id
            result.disposition = Disposition.HUMAN_REVIEW
            result.final_response = None
        else:
            result.draft = run_draft(ticket, result.intake, result.knowledge, trace, usage_log)
            result.quality = run_quality(result.draft, result.knowledge, ticket, trace, usage_log)
            if result.quality.passed:
                result.final_response = result.draft.text
                result.actions_taken = run_actions(ticket, result.intake, result.decision,
                                                   result.final_response, trace)
                result.disposition = Disposition.AUTO_RESOLVED
                audit_log("auto_resolved", {"ticket_id": ticket.id,
                                            "risk": result.decision.risk_score})
            else:
                review = run_escalation(ticket, result.decision, result.draft, trace)
                result.review_id = review.id
                result.disposition = "human_review"
                audit_log("quality_failed_escalated", {
                    "ticket_id": ticket.id, "issues": result.quality.issues})

    except Exception as exc:  # surface failures, never swallow
        result.error = f"{type(exc).__name__}: {exc}"
        audit_log("pipeline_error", {"ticket_id": ticket.id, "error": result.error})

    result.trace = trace
    result.total_cost_usd = round(sum(u.cost_usd for u in usage_log), 6)
    result.total_latency_ms = sum(t.duration_ms for t in trace)
    for u in usage_log:
        store.append("usage", u.model_dump())
    store.append("runs", result.model_dump())
    return result
