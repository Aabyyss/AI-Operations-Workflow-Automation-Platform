"""FastAPI service — the production integration surface.

Everything the n8n workflows, the dashboard, and Power BI need lives here.
Run: uvicorn backend.main:app --reload
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

from . import config, pipeline, store
from .analyzer import analyze_process
from .integrations import actions as integ
from .integrations.audit import audit_log
from .models import PipelineResult, ProcessInput, ReviewDecision, Ticket, iso_now
from .rag import retriever
from .store import storage

app = FastAPI(
    title="AI Operations & Workflow Automation Platform",
    description=(
        "Side A: process analysis, automation scoring, ROI/cost engine. "
        "Side B: agentic support pipeline with RAG, risk-gated execution, "
        "human-in-the-loop approvals, and cost/quality monitoring."
    ),
    version="0.1.0",
)

DASHBOARD = Path(__file__).resolve().parent.parent / "dashboard" / "index.html"


# ---------------------------------------------------------------------------
# Side A — process analysis & ROI
# ---------------------------------------------------------------------------
@app.post("/api/analyze")
def analyze(p: ProcessInput) -> dict:
    analysis, usage = analyze_process(p)
    for u in usage:
        storage.append("usage", u.model_dump())
    storage.append("analyses", analysis.model_dump())
    audit_log("process_analyzed", {"process_id": analysis.process_id, "name": p.name,
                                   "score": analysis.score.total})
    return analysis.model_dump()


@app.get("/api/analyses")
def list_analyses() -> list[dict]:
    return storage.all("analyses")


# ---------------------------------------------------------------------------
# Side B — ticket pipeline
# ---------------------------------------------------------------------------
@app.post("/api/tickets", response_model=PipelineResult)
def submit_ticket(ticket: Ticket) -> PipelineResult:
    result = pipeline.run_pipeline(ticket)
    return result


@app.get("/api/tickets")
def list_tickets() -> list[dict]:
    return storage.all("tickets")


@app.get("/api/tickets/{ticket_id}")
def get_ticket(ticket_id: str) -> dict:
    run = next((r for r in reversed(storage.all("runs")) if r.get("ticket_id") == ticket_id), None)
    if not run:
        raise HTTPException(404, "no run found for ticket")
    return run


@app.post("/api/tickets/demo")
def run_demo_ticket() -> dict:
    """Submit the canonical demo ticket (duplicate charge refund)."""
    ticket = Ticket(
        customer_email="jordan.miles@northwind.example",
        subject="Charged twice for my subscription",
        body=(
            "I've been charged twice for my subscription this month — $49.00 on "
            "the 3rd and again on the 5th. I need one of the charges refunded. "
            "My account email is jordan.miles@northwind.example."
        ),
        channel="email",
    )
    return pipeline.run_pipeline(ticket).model_dump()


# ---------------------------------------------------------------------------
# Human-in-the-loop approvals
# ---------------------------------------------------------------------------
@app.get("/api/reviews")
def list_reviews(status: str | None = None) -> list[dict]:
    reviews = storage.all("reviews")
    if status:
        reviews = [r for r in reviews if r.get("status") == status]
    return reviews


@app.post("/api/reviews/{review_id}/decision")
def decide_review(review_id: str, decision: ReviewDecision) -> dict:
    review = storage.get("reviews", review_id)
    if not review:
        raise HTTPException(404, "review not found")
    if review["status"] != "pending":
        raise HTTPException(409, f"review already {review['status']}")

    patch = {
        "status": "approved" if decision.note != "REJECT" else "rejected",
        "reviewed_by": decision.reviewer,
        "decision_note": decision.note,
        "reviewed_at": iso_now(),
    }
    # Explicit reject flag keeps the API honest without extra models.
    if decision.note and decision.note.upper().startswith("REJECT"):
        patch["status"] = "rejected"
    updated = storage.update("reviews", review_id, patch)
    audit_log("review_decided", {"review_id": review_id, "status": patch["status"],
                                 "reviewer": decision.reviewer})

    runs = [r for r in storage.all("runs") if r.get("ticket_id") == review["ticket_id"]]
    run_id = runs[-1].get("id") if runs else None

    executed: list[str] = []
    if patch["status"] == "approved":
        # Rebuild minimal context to execute the approved actions.
        ticket_data = storage.get("tickets", review["ticket_id"])
        if ticket_data:
            from .models import IntakeResult
            ticket = Ticket(**ticket_data)
            runs = [r for r in storage.all("runs") if r.get("ticket_id") == ticket.id]
            decision_data = runs[-1]["decision"] if runs else None
            if decision_data:
                from .models import DecisionResult
                dec = DecisionResult(**decision_data)
                dec.proposed_actions = review.get("proposed_actions", dec.proposed_actions)
                trace: list = []
                executed = pipeline.run_actions(ticket, IntakeResult(category="billing",
                                                                     priority="medium",
                                                                     intent="manual",
                                                                     confidence=1.0),
                                                dec, review["proposed_response"], trace)
                # Record final disposition on the run.
                if run_id:
                    storage.update("runs", run_id, {"disposition": "approved_executed",
                                                    "final_response": review["proposed_response"]})
    return {"review": updated, "executed_actions": executed}


# ---------------------------------------------------------------------------
# Monitoring & analytics (Power BI feed)
# ---------------------------------------------------------------------------
@app.get("/api/usage")
def usage_records() -> list[dict]:
    return storage.all("usage")


@app.get("/api/audit")
def audit_records(limit: int = 200) -> list[dict]:
    return storage.all("audit")[-limit:]


@app.get("/api/outbox")
def outbox_records() -> list[dict]:
    return storage.all("outbox")


@app.get("/api/analytics/summary")
def analytics_summary() -> dict:
    usage = pd.DataFrame(storage.all("usage"))
    runs = pd.DataFrame(storage.all("runs"))
    reviews = storage.all("reviews")
    analyses = storage.all("analyses")

    by_agent = (
        usage.groupby("agent")
        .agg(calls=("id", "count"), tokens_in=("tokens_in", "sum"),
             tokens_out=("tokens_out", "sum"), cost_usd=("cost_usd", "sum"))
        .round(4).reset_index().to_dict("records")
    ) if not usage.empty else []

    dispositions = runs["disposition"].value_counts().to_dict() if not runs.empty else {}
    automation_rate = (
        100.0 * dispositions.get("auto_resolved", 0)
        / max(1, sum(dispositions.values()))
    )
    avg_latency = float(runs["total_latency_ms"].mean().round(1)) if not runs.empty else 0.0
    pending_reviews = sum(1 for r in reviews if r.get("status") == "pending")

    latest_roi = analyses[-1]["costs"] if analyses else None
    return {
        "tickets_processed": int(len(runs)),
        "dispositions": dispositions,
        "automation_rate_pct": round(automation_rate, 1),
        "avg_pipeline_latency_ms": avg_latency,
        "pending_reviews": pending_reviews,
        "llm_cost_by_agent": by_agent,
        "total_llm_cost_usd": round(float(usage["cost_usd"].sum()), 4) if not usage.empty else 0.0,
        "estimated_monthly_savings_usd": (latest_roi or {}).get("monthly_savings_usd", 0),
        "mode": config.MODE,
        "knowledge_chunks": len(retriever.index.docs),
    }


@app.post("/api/knowledge/reload")
def reload_knowledge() -> dict:
    n = retriever.reload()
    audit_log("knowledge_reloaded", {"chunks": n})
    return {"chunks": n}


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def dashboard() -> FileResponse:
    return FileResponse(DASHBOARD)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "mode": config.MODE}
