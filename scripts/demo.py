"""End-to-end demo: Side A (analyze + ROI) and Side B (pipeline + HITL).

Run:  python -m scripts.demo
Resets the demo data directory first so numbers are reproducible.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datetime import datetime, timezone  # noqa: E402

from backend import config, designer, pipeline  # noqa: E402
from backend.analytics import approval_sla_metrics  # noqa: E402
from backend.analyzer import analyze_process  # noqa: E402
from backend.models import (ProcessInput, ProcessStep, Ticket,  # noqa: E402
                            WorkflowDesignRequest)
from backend.run_metrics import run_performance_metrics  # noqa: E402
from backend.store import storage  # noqa: E402


def reset_data() -> None:
    if config.DATA_DIR.exists():
        shutil.rmtree(config.DATA_DIR)
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    # Rebind the storage singleton after wiping the directory.
    from backend.store import Storage
    storage.__dict__.update(Storage().__dict__)


def side_a() -> None:
    print("=" * 68)
    print("SIDE A - Should we automate this? (process analysis + ROI)")
    print("=" * 68)
    p = ProcessInput(
        name="Customer Support Ticket Handling",
        department="support",
        monthly_volume=1500,
        hourly_rate_usd=25.0,
        steps=[
            ProcessStep(name="Read ticket", minutes_per_item=1.5, touches_pii=True),
            ProcessStep(name="Categorize ticket", minutes_per_item=1.0),
            ProcessStep(name="Search knowledge base", minutes_per_item=2.5, requires_judgment=True),
            ProcessStep(name="Draft response", minutes_per_item=4.0, requires_judgment=True),
            ProcessStep(name="Process refund / update CRM", minutes_per_item=2.0,
                        touches_money=True, touches_pii=True),
            ProcessStep(name="Manager review of hard cases", minutes_per_item=3.0,
                        repetitive=False, requires_judgment=True),
        ],
    )
    analysis, _ = analyze_process(p)
    storage.append("analyses", analysis.model_dump())  # API route does this too
    s, c = analysis.score, analysis.costs
    print(f"\nProcess: {analysis.name}")
    print(f"Automation score : {s.total}/100 ({s.verdict})")
    print(f"  volume={s.volume_score} repetitive={s.repetitiveness_score} "
          f"structure={s.structure_score} judgment={s.judgment_penalty} risk={s.risk_penalty}")
    print(f"\nCurrent monthly cost : ${c.current_monthly_cost_usd:>10,.2f}")
    print(f"AI-assisted monthly  : ${c.ai_monthly_cost_usd:>10,.2f}")
    print(f"Monthly savings      : ${c.monthly_savings_usd:>10,.2f}")
    print(f"Implementation (1x)  : ${c.implementation_cost_usd:>10,.2f}")
    print(f"Payback              : {c.payback_months} months")
    print(f"First-year ROI       : {c.first_year_roi_pct}%")
    print("\nStep mapping:")
    for m in analysis.ai_mapping:
        print(f"  - {m['step']:<32} -> {m['recommendation']}")
    print("\nAssumptions:")
    for a in analysis.assumptions:
        print(f"  - {a}")

    # The bridge from Side A to Side B: emit the importable n8n workflow.
    wf = designer.design_from_request(
        analysis.model_dump(), WorkflowDesignRequest(process_id=analysis.process_id))
    print(f"\nGenerated n8n workflow: {wf['name']}")
    print(f"  nodes: {wf['node_count']} (intake webhook -> agentic pipeline -> "
          f"risk-gated escalation, hourly monitoring digest, AI-mapping note)")
    print(f"  import via n8n: File -> Import from File (GET "
          f"/api/workflows/{{id}}/download)")


def _ticket(subject: str, body: str, email: str) -> Ticket:
    return Ticket(customer_email=email, subject=subject, body=body)


def side_b() -> None:
    print("\n" + "=" * 68)
    print("SIDE B - How do we run it safely? (agentic pipeline + HITL)")
    print("=" * 68)
    tickets = [
        _ticket("Charged twice for my subscription",
                "I've been charged twice this month - $49.00 on the 3rd and again "
                "on the 5th. I need one of the charges refunded.",
                "jordan.miles@northwind.example"),
        _ticket("Can't sign in",
                "I forgot my password and now I can't access my account. "
                "Can you help me reset it?",
                "priya.n@acmecorp.example"),
        _ticket("API returning 429 errors",
                "The API keeps returning 429 errors since this morning. "
                "Is something wrong on your side?",
                "dev@integrationco.example"),
        _ticket("Refund for annual plan",
                "I cancelled but was charged $1,200.00 for the annual plan. "
                "I need a full refund immediately.",
                "ops@bigretail.example"),
        _ticket("Remove 2FA from my account",
                "I lost my phone and need you to disable two-factor "
                "authentication on my account.",
                "tina@designstudio.example"),
        _ticket("Pricing question",
                "How much does the Growth plan cost per user? Do you offer "
                "nonprofit discounts?",
                "hello@newstartup.example"),
    ]
    # One batch request, exactly as n8n or an email export would submit them.
    from fastapi.testclient import TestClient  # reuse the same route logic
    from backend.main import app
    with TestClient(app) as client:
        out = client.post("/api/tickets/batch",
                          json={"tickets": [t.model_dump() for t in tickets]}).json()
    print(f"\nBatch summary: {out['summary']}")
    for t, r in zip(tickets, out["results"]):
        risk = r["decision"]["risk_score"] if r.get("decision") else "-"
        print(f"\n[{t.subject}]")
        print(f"  disposition={r['disposition']}  risk={risk}  "
              f"cost=${r['total_cost_usd']:.5f}  latency={r['total_latency_ms']}ms")
        if r["disposition"] == "auto_resolved":
            print(f"  actions: {', '.join(r['actions_taken'])}")
        elif r.get("review_id"):
            reason = r["decision"]["reason"] if r.get("decision") else ""
            print(f"  escalated -> {r['review_id']} ({reason})")
        if r.get("error"):
            print(f"  ERROR: {r['error']}")


def approve_one() -> None:
    print("\n" + "=" * 68)
    print("HUMAN-IN-THE-LOOP - approving the queued large refund")
    print("=" * 68)
    pending = [r for r in storage.all("reviews") if r["status"] == "pending"]
    if not pending:
        print("No pending reviews.")
        return
    review = pending[0]
    print(f"\nReview {review['id']} (ticket {review['ticket_id']}, risk {review['risk_score']})")
    print(f"Reason: {review['reason']}")
    print(f"Proposed: {review['proposed_actions']}")
    print(f"Draft response starts: \"{review['proposed_response'][:120]}...\"")

    from fastapi.testclient import TestClient  # reuse the same route logic
    from backend.main import app
    with TestClient(app) as client:
        out = client.post(f"/api/reviews/{review['id']}/decision",
                          json={"reviewer": "manager_amy", "note": "APPROVE"}).json()
    print(f"\nDecision: {out['review']['status']} by manager_amy")
    print(f"Executed: {out['executed_actions']}")


def summary() -> None:
    print("\n" + "=" * 68)
    print("MONITORING - is it actually working? (/api/analytics/summary)")
    print("=" * 68)
    from backend.main import analytics_summary
    s = analytics_summary()
    print(f"\nTickets processed : {s['tickets_processed']}")
    print(f"Dispositions      : {s['dispositions']}")
    print(f"Automation rate   : {s['automation_rate_pct']}%")
    print(f"Pending approvals : {s['pending_reviews']}")
    print(f"Avg latency       : {s['avg_pipeline_latency_ms']}ms")
    print(f"Total LLM cost    : ${s['total_llm_cost_usd']:.4f} (mock-mode estimates)")
    print("\nCost by agent:")
    for row in s["llm_cost_by_agent"]:
        print(f"  {row['agent']:<8} calls={row['calls']:<3} "
              f"tokens={row['tokens_in']}/{row['tokens_out']}  cost=${row['cost_usd']:.5f}")
    print(f"\nEstimated monthly savings (latest analysis): "
          f"${s['estimated_monthly_savings_usd']:,}")

    runs = storage.all("runs")
    sla = approval_sla_metrics(storage.all("reviews"), len(runs),
                               datetime.now(timezone.utc))
    perf = run_performance_metrics(runs)
    print("\nApproval-queue SLA (GET /api/analytics/approvals):")
    print(f"  pending={sla['pending']}  oldest={sla['oldest_pending_minutes']}m  "
          f"aging={sla['aging_buckets']}")
    print(f"  human turnaround median={sla['turnaround_minutes']['median']}m  "
          f"escalation rate={sla['escalation_rate_pct']}%")
    print("\nPipeline performance (GET /api/analytics/runs):")
    print(f"  latency p50={perf['latency_ms']['p50']}ms  "
          f"p95={perf['latency_ms']['p95']}ms  max={perf['latency_ms']['max']}ms")
    print(f"  containment={perf['containment_rate_pct']}%  "
          f"human-touch={perf['human_touch_rate_pct']}%  "
          f"failure={perf['failure_rate_pct']}%")
    print(f"  cost per run mean=${perf['cost_per_run_usd']['mean']:.5f}  "
          f"p95=${perf['cost_per_run_usd']['p95']:.5f}")
    print("\nBI exports: GET /api/export/runs.csv, approvals.csv, usage.csv")
    print("Data written to ./data/ -> Power BI / n8n can consume via the API.")
    print("Start the API with: uvicorn backend.main:app --reload")


if __name__ == "__main__":
    reset_data()
    side_a()
    side_b()
    approve_one()
    summary()
