"""FastAPI service — the production integration surface.

Everything the n8n workflows, the dashboard, and Power BI need lives here.
Run: uvicorn backend.main:app --reload
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               Response, StreamingResponse)
from pydantic import BaseModel

from . import config, designer, exports, metrics, outbound, pipeline, ratelimit, reqlog, security, store
from .security import require_admin, require_approver
from .analytics import approval_sla_metrics
from .budget import budget_status
from .drift import drift_report
from .analyzer import analyze_process
from .run_metrics import feedback_metrics, run_performance_metrics
from .integrations import actions as integ
from .integrations.audit import audit_log
from .models import (CostComparisonRequest, Feedback, PipelineResult,
                     ProcessInput, ReviewDecision, Ticket,
                     WorkflowDesignRequest, iso_now)
from .rag import retriever
from .retention import COLLECTION_LIMITS, select_for_prune
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

security.install_auth(app)
ratelimit.install_rate_limit(app)
reqlog.install_request_logging(app)

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
# Workflow designer — Analysis → importable n8n workflow
# ---------------------------------------------------------------------------
@app.post("/api/workflows/design")
def design(req: WorkflowDesignRequest) -> dict:
    # Analyses are keyed by process_id, not id — match explicitly.
    analysis = next((a for a in storage.all("analyses")
                     if a.get("process_id") == req.process_id), None)
    if not analysis:
        raise HTTPException(404, "analysis not found — POST /api/analyze first")
    design_record = designer.design_from_request(analysis, req,
                                                 base_url=config.API_BASE_URL)
    storage.append("workflows", {k: v for k, v in design_record.items()
                                 if k != "workflow"})
    audit_log("workflow_designed", {"process_id": req.process_id,
                                    "workflow_id": design_record["id"],
                                    "nodes": design_record["node_count"]})
    return design_record


@app.get("/api/workflows")
def list_workflows() -> list[dict]:
    return storage.all("workflows")


@app.get("/api/workflows/{workflow_id}/download")
def download_workflow(workflow_id: str):
    """The JSON to save as a file and import via n8n → Import from File."""
    record = storage.get("workflows", workflow_id)
    if not record:
        raise HTTPException(404, "workflow not found")
    analysis = storage.get("analyses", record["process_id"]) or {"process_id": record["process_id"]}
    full = designer.design_workflow(analysis, name=record["name"])
    return full["workflow"]


# ---------------------------------------------------------------------------
# Side B — ticket pipeline
# ---------------------------------------------------------------------------
@app.post("/api/tickets", response_model=PipelineResult)
def submit_ticket(ticket: Ticket) -> PipelineResult:
    result = pipeline.run_pipeline(ticket)
    return result


@app.post("/api/webhooks/tickets")
async def submit_signed_ticket(request: Request) -> dict:
    """Signed ticket intake — the endpoint the n8n bridge should target.

    Body: a single Ticket JSON. When AIOPS_WEBHOOK_SECRET is set the request
    must carry X-AIOPS-Signature: sha256=<ts>.<hmac> over the raw bytes
    (see backend/webhook.py); unset = accepted unsigned.
    """
    from .models import Ticket as TicketModel
    from .webhook import WebhookAuthError, read_signed_body

    try:
        body = await read_signed_body(request)
    except WebhookAuthError as exc:
        audit_log("webhook_rejected", {"reason": str(exc),
                                       "ip": request.client.host if request.client else ""})
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    ticket = TicketModel.model_validate_json(body)
    result = pipeline.run_pipeline(ticket)
    return result.model_dump()


BATCH_MAX = 100


class TicketBatch(BaseModel):
    tickets: list[Ticket]


@app.post("/api/tickets/batch")
def submit_batch(batch: TicketBatch) -> dict:
    """Process many tickets in one request (n8n fan-in, email exports).

    Per-item results are preserved: one bad ticket never blocks the rest.
    """
    if not batch.tickets:
        raise HTTPException(422, "tickets must not be empty")
    if len(batch.tickets) > BATCH_MAX:
        raise HTTPException(422, f"batch limited to {BATCH_MAX} tickets")

    results: list[PipelineResult] = []
    for t in batch.tickets:
        try:
            results.append(pipeline.run_pipeline(t))
        except Exception as exc:  # noqa: BLE001 — isolate per item
            results.append(PipelineResult(ticket_id=t.id,
                                          disposition="failed",
                                          error=str(exc)))

    dispositions = [r.disposition.value for r in results]
    summary = {
        "submitted": len(results),
        "auto_resolved": dispositions.count("auto_resolved"),
        "human_review": dispositions.count("human_review"),
        "failed": dispositions.count("failed"),
        "total_cost_usd": round(sum(r.total_cost_usd for r in results), 5),
        "total_latency_ms": sum(r.total_latency_ms for r in results),
    }
    audit_log("batch_submitted", {"size": len(results), **summary})
    return {"summary": summary,
            "results": [r.model_dump() for r in results]}


@app.get("/api/tickets")
def list_tickets() -> list[dict]:
    return storage.all("tickets")


@app.get("/api/tickets/{ticket_id}")
def get_ticket(ticket_id: str) -> dict:
    run = next((r for r in reversed(storage.all("runs")) if r.get("ticket_id") == ticket_id), None)
    if not run:
        raise HTTPException(404, "no run found for ticket")
    return run


@app.get("/api/runs")
def list_runs(limit: int = 50) -> list[dict]:
    """Full pipeline run records — the BI 'Runs' table feed."""
    return storage.all("runs")[-limit:]


@app.get("/api/runs/stream")
def stream_runs(limit: int = 0, poll_seconds: float = 0.5,
                max_wait_seconds: float = 10.0) -> Response:
    """Server-Sent Events tail of the runs store.

    The dashboard (or an n8n Execute Workflow trigger) subscribes once and
    receives every new run as it lands — no polling loops in clients.
    Hand-rolled on StreamingResponse: an SSE frame is just `data:` lines,
    and the platform stays at zero extra dependencies. With limit=0 the
    stream stays open until the client disconnects; a positive limit
    closes after that many events, which also makes it testable.
    """
    import asyncio
    import json as _json

    def _frame(r: dict) -> str:
        payload = _json.dumps({k: r[k] for k in
                               ("id", "ticket_id", "disposition",
                                "total_cost_usd", "total_latency_ms")
                               if k in r})
        return f"event: run\nid: {r['id']}\ndata: {payload}\n\n"

    async def gen():
        seen: set[str] = set()
        sent = 0
        # Backfill the most recent runs so a fresh subscriber gets instant
        # context, then tail the store for anything new.
        for r in storage.all("runs")[-(limit or 10):]:
            seen.add(r["id"])
            yield _frame(r)
            sent += 1
            if limit and sent >= limit:
                return
        deadline = asyncio.get_event_loop().time() + max_wait_seconds
        while True:
            for r in storage.all("runs"):
                if r["id"] in seen:
                    continue
                seen.add(r["id"])
                yield _frame(r)
                sent += 1
                if limit and sent >= limit:
                    return
            if asyncio.get_event_loop().time() > deadline:
                return
            await asyncio.sleep(poll_seconds)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@app.post("/api/runs/{run_id}/replay")
def replay_run(run_id: str) -> dict:
    """Re-execute the original ticket of a stored run and compare.

    Why: a prompt change, a new policy doc or a threshold tweak should be
    provable against real past tickets, not hoped for. The replay is a
    *new* run (new id, fresh audit) — never a mutation of history — and
    the comparison keeps governance honest: if the disposition or the
    refund amount changes after a 'harmless' prompt edit, this endpoint
    is where that becomes visible.
    """
    record = storage.get("runs", run_id)
    if not record:
        raise HTTPException(404, f"run not found: {run_id}")
    ticket_data = storage.get("tickets", record["ticket_id"])
    if not ticket_data:
        raise HTTPException(409, "original ticket no longer stored (pruned?)")

    ticket = Ticket.model_validate(ticket_data)
    rerun = pipeline.run_pipeline(ticket)
    prior = {
        "run_id": record["id"],
        "disposition": record["disposition"],
        "total_cost_usd": record.get("total_cost_usd", 0.0),
        "total_latency_ms": record.get("total_latency_ms", 0.0),
        "final_response": record.get("final_response"),
        "actions_taken": record.get("actions_taken", []),
    }
    after = {
        "run_id": rerun.id,
        "disposition": rerun.disposition.value,
        "total_cost_usd": rerun.total_cost_usd,
        "total_latency_ms": rerun.total_latency_ms,
        "final_response": rerun.final_response,
        "actions_taken": rerun.actions_taken,
    }
    audit_log("run_replayed", {"original_run_id": run_id, "replay_run_id": rerun.id})
    return {
        "original": prior,
        "replay": after,
        "identical_disposition": prior["disposition"] == after["disposition"],
        "identical_response": prior["final_response"] == after["final_response"],
    }


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
def decide_review(review_id: str, decision: ReviewDecision,
                  _role: str = Depends(require_approver)) -> dict:
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
@app.get("/api/analytics/approvals")
def approval_sla() -> dict:
    """Human-approval queue SLA: aging, turnaround, escalation rate."""
    from datetime import datetime, timezone
    reviews = storage.all("reviews")
    runs_count = len(storage.all("runs"))
    return approval_sla_metrics(reviews, runs_count, datetime.now(timezone.utc))


@app.get("/api/analytics/runs")
def run_analytics() -> dict:
    """Pipeline performance: latency percentiles, failure/containment, cost,
    plus the operator-satisfaction signal from run feedback."""
    runs = storage.all("runs")
    out = run_performance_metrics(runs)
    out["feedback"] = feedback_metrics(storage.all("feedback"), runs)
    return out


@app.get("/api/usage")
def usage_records() -> list[dict]:
    return storage.all("usage")


@app.get("/api/analytics/drift")
def drift_canary(window: int = 50) -> dict:
    """Recent-vs-baseline behavior comparison — alerts when containment,
    escalation or failure rates swing beyond the threshold."""
    return drift_report(storage.all("runs"), window=max(5, min(window, 500)))


@app.get("/api/analytics/budget")
def budget_analytics() -> dict:
    """LLM spend vs the configured monthly budget (alerts at 100% spend
    or a 120% projection). Disabled unless AIOPS_MONTHLY_BUDGET_USD > 0."""
    return budget_status(storage.all("usage"), config.MONTHLY_BUDGET_USD)


@app.get("/metrics")
def prometheus_metrics() -> Response:
    """Prometheus scrape endpoint (text/plain, version 0.0.4)."""
    body = metrics.render_prometheus(
        runs=storage.all("runs"),
        reviews=storage.all("reviews"),
        usage=storage.all("usage"),
        feedback=storage.all("feedback"),
        outbox=storage.all("outbox"),
        knowledge_chunks=len(retriever.index.docs),
    )
    return Response(content=body, media_type=metrics.CONTENT_TYPE)


@app.get("/api/audit")
def audit_records(limit: int = 200) -> list[dict]:
    return storage.all("audit")[-limit:]


@app.get("/api/outbox")
def outbox_records() -> list[dict]:
    """Queued outbound actions with delivery state folded in."""
    return outbound.outbox_view()


@app.post("/api/outbox/deliver")
def deliver_outbox() -> dict:
    """Attempt signed delivery of pending outbox records (cron/n8n target)."""
    summary = outbound.deliver_pending()
    return summary


@app.get("/api/deliveries")
def delivery_ledger(limit: int = 100) -> list[dict]:
    """Audit-grade ledger of every outbound delivery attempt."""
    return storage.all("deliveries")[-limit:]


@app.post("/api/outbox/{outbox_id}/retry")
def retry_outbox(outbox_id: str) -> dict:
    """Reset a dead/failing delivery to pending with a fresh retry budget."""
    record = outbound.retry(outbox_id)
    if record is None:
        raise HTTPException(404, f"no retryable outbox record: {outbox_id}")
    audit_log("outbox_retried", {"outbox_id": outbox_id})
    return record


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
    avg_latency = float(runs["total_latency_ms"].mean().round(3)) if not runs.empty else 0.0
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


@app.post("/api/admin/prune")
def prune_collections(confirm: bool = False,
                      _role: str = Depends(require_admin)) -> dict:
    """Apply retention policy (dry-run by default; confirm=true to execute)."""
    from .models import iso_now

    report: dict[str, int] = {}
    for collection, (max_age_days, max_records) in COLLECTION_LIMITS.items():
        records = storage.all(collection)
        keep, prune = select_for_prune(records, max_age_days, max_records)
        report[collection] = len(prune)
        if confirm and prune:
            storage.replace_all(collection, keep)
    if confirm:
        audit_log("retention_pruned", {**report, "ts": iso_now()})
    return {"mode": "executed" if confirm else "dry-run",
            "would_prune" if not confirm else "pruned": report}


@app.post("/api/knowledge/reload")
def reload_knowledge() -> dict:
    n = retriever.reload()
    audit_log("knowledge_reloaded", {"chunks": n})
    return {"chunks": n}


# --------------------------------------------------- knowledge management


class KnowledgeDoc(BaseModel):
    name: str
    markdown: str
    overwrite: bool = False


@app.get("/api/knowledge")
def list_knowledge() -> list[dict]:
    """Every policy document: id, title, chunk count, size, last modified."""
    from .knowledge import list_documents

    return list_documents()


@app.get("/api/knowledge/{doc_id}")
def get_knowledge(doc_id: str) -> dict:
    from .knowledge import read_document

    try:
        doc = read_document(doc_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if doc is None:
        raise HTTPException(404, f"document not found: {doc_id}")
    return doc


@app.post("/api/knowledge", status_code=201)
def save_knowledge(doc: KnowledgeDoc,
                   _role: str = Depends(require_admin)) -> dict:
    """Create or update a policy document, then rebuild the RAG index.

    The pipeline retrieves from this corpus on the very next ticket —
    no redeploy, no restart. Overwriting an existing document requires
    an explicit overwrite=true.

    Corpus writes shape what the AI tells customers, so they are an
    admin mutation: operator or admin key required (reads stay open to
    every valid role).
    """
    from . import knowledge

    try:
        result = knowledge.save_document(doc.name, doc.markdown, doc.overwrite)
    except knowledge.DocumentExistsError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    n = retriever.reload()
    audit_log("knowledge_saved", {"doc_id": result["doc_id"],
                                   "created": result["created"], "chunks": n})
    return {**result, "index_chunks": n}


@app.delete("/api/knowledge/{doc_id}")
def delete_knowledge(doc_id: str,
                     _role: str = Depends(require_admin)) -> dict:
    from . import knowledge

    try:
        removed = knowledge.delete_document(doc_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if not removed:
        raise HTTPException(404, f"document not found: {doc_id}")
    n = retriever.reload()
    audit_log("knowledge_deleted", {"doc_id": doc_id, "chunks": n})
    return {"deleted": doc_id, "index_chunks": n}


# --------------------------------------------------------------- A/B cycle time


class ManualCycleRecord(BaseModel):
    """One manually-handled ticket, stamped by whoever tracked it."""
    ticket_id: str | None = None
    label: str = ""  # what made this ticket comparable, e.g. 'duplicate charge'
    cycle_seconds: float


@app.post("/api/analytics/ab/records", status_code=201)
def record_manual_cycle(rec: ManualCycleRecord) -> dict:
    """Stamp a cycle time for the manual cohort (human handled it, no AI)."""
    from .ab_testing import COHORTS

    if rec.cycle_seconds <= 0:
        raise HTTPException(422, "cycle_seconds must be positive")
    record = rec.model_dump()
    record["id"] = f"cyc_{len(storage.all('cycle_times')) + 1}"
    record["cohort"] = "manual"
    record["recorded_at"] = iso_now()
    storage.append("cycle_times", record)
    audit_log("cycle_time_recorded", {"cohort": "manual",
                                       "cycle_seconds": rec.cycle_seconds})
    return record


@app.get("/api/analytics/quality")
def quality_gate_metrics() -> dict:
    """How often is the human-in-the-loop gate right?

    Precision comes from review decisions (rejected escalations / decided
    escalations); the recall proxy counts auto-resolved runs that later
    drew thumbs-down feedback — escalations that should have happened.
    """
    from .quality_gate import gate_quality

    return gate_quality(storage.all("reviews"), storage.all("runs"),
                        storage.all("feedback"))


@app.get("/api/analytics/ab")
def ab_cycle_time() -> dict:
    """AI-assisted vs manual cycle time — measured, not estimated.

    The ai_assisted cohort reads cycle_seconds stamped on pipeline runs
    by the submitting system; the manual cohort is what operators record.
    Below the minimum cohort size the report says 'not yet' instead of
    inventing a percentage.
    """
    from .ab_testing import ab_report

    return ab_report(storage.all("runs"), storage.all("cycle_times"))


# ------------------------------------------------- market cost comparison
@app.get("/api/analytics/market-plans")
def market_plans() -> dict:
    """Published list prices for the AI-support plans we compare against.

    A dated snapshot with a source per entry (see
    docs/COMPETITIVE_ANALYSIS.md §3) — a business case that cites nothing
    is a brochure. Callers may override any rate on the request instead of
    editing the catalog.
    """
    from .cost_compare import PRICE_SNAPSHOT, list_market_plans

    return {"price_snapshot": PRICE_SNAPSHOT, "plans": list_market_plans()}


@app.post("/api/analytics/cost-comparison")
def cost_comparison(req: CostComparisonRequest) -> dict:
    """Model what market AI-support pricing costs at *your* volume against
    what this pipeline actually costs per decision.

    Answers the question per-outcome pricing makes hard: at what resolution
    rate does paying per resolution overtake paying per decision? Our side
    is read from the run ledger, never estimated, and the report names the
    plans that come out cheaper than us.
    """
    from .cost_compare import compare_costs

    result = compare_costs(storage.all("runs"), req)
    audit_log("cost_comparison_modelled", {
        "monthly_volume": req.monthly_volume,
        "resolution_rate_pct": req.resolution_rate_pct,
        "seats": req.seats,
        "our_monthly_usd": result.our_monthly_usd,
        "measurement_basis": result.measurement_basis,
        "plans_modelled": [p.slug for p in result.plans],
        "plans_that_beat_us": result.plans_that_beat_us,
    })
    return result.model_dump()


# --------------------------------------------------------------- feedback
@app.post("/api/runs/{run_id}/feedback", status_code=201)
def submit_feedback(run_id: str, fb: Feedback) -> dict:
    """Attach operator feedback to a finished run."""
    if not storage.get("runs", run_id):
        raise HTTPException(404, f"run not found: {run_id}")
    fb.run_id = run_id
    storage.append("feedback", fb.model_dump())
    audit_log("feedback_recorded", {"feedback_id": fb.id, "run_id": run_id,
                                    "rating": fb.rating})
    return fb.model_dump()


@app.get("/api/feedback")
def list_feedback() -> list[dict]:
    """All feedback, newest first."""
    return sorted(storage.all("feedback"), key=lambda f: f.get("created_at", ""),
                  reverse=True)


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def dashboard() -> FileResponse:
    return FileResponse(DASHBOARD)


# ---------------------------------------------------------------------------
# CSV exports — flat, schema-pinned feeds for Power BI refresh
# ---------------------------------------------------------------------------
def _csv_response(text: str, filename: str) -> Response:
    return Response(
        content=text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/export/runs.csv")
def export_runs() -> Response:
    return _csv_response(exports.runs_csv(storage.all("runs")), "aiops_runs.csv")


@app.get("/api/export/approvals.csv")
def export_approvals() -> Response:
    return _csv_response(exports.reviews_csv(storage.all("reviews")),
                         "aiops_approvals.csv")


@app.get("/api/export/usage.csv")
def export_usage() -> Response:
    return _csv_response(exports.usage_csv(storage.all("usage")), "aiops_usage.csv")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "mode": config.MODE}


@app.get("/api/health")
def api_health() -> dict:
    """Component health: what the operator actually asks at 2am.

    /health answers liveness and /ready answers routability; this one
    answers "which part is misbehaving?" — storage, knowledge corpus,
    approval backlog, outbound delivery, LLM gateway, budget posture —
    with a machine-readable status per component.
    """
    from .health import health_report

    return health_report()


def _ready_body() -> dict:
    """Pure readiness checks — no HTTP concerns."""
    checks: dict = {}

    probe = config.DATA_DIR / ".readiness_probe"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        checks["storage_writable"] = True
    except OSError:
        checks["storage_writable"] = False

    checks["knowledge_chunks"] = len(retriever.index.docs)

    ok = checks["storage_writable"] and checks["knowledge_chunks"] > 0
    return {"status": "ready" if ok else "not_ready",
            "mode": config.MODE, "checks": checks}


@app.get("/ready")
def ready() -> dict:
    """Readiness probe: process is up AND its dependencies actually work.

    /health answers 'can I accept connections?' (liveness);
    /ready answers 'should traffic be routed to me?' (readiness).
    A deployment whose knowledge corpus failed to load or whose data
    directory is read-only is alive but not ready.
    """
    body = _ready_body()
    return body if body["status"] == "ready" else JSONResponse(
        status_code=503, content=body)
