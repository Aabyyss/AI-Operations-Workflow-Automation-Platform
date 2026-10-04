"""Component health: what the operator actually asks at 2am.

`/ready` answers "is this instance routable?". This report answers
"what is the state of each moving part right now?" — storage writability,
knowledge corpus, pending approval queue, outbound delivery queue, LLM
gateway mode, and monthly budget posture. Every check reports a
machine-readable status so the dashboard and any uptime monitor can
render it without string-parsing prose.
"""
from __future__ import annotations

from . import config, outbound
from .rag import retriever
from .store import storage


def health_report() -> dict:
    checks: dict[str, dict] = {}

    # Storage: same writability probe /ready uses, plus record counts.
    probe = config.DATA_DIR / ".health_probe"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        checks["storage"] = {"status": "ok", "records": {
            c: len(storage.all(c)) for c in storage.collection_names()}}
    except OSError as exc:
        checks["storage"] = {"status": "error", "error": str(exc)}

    # Knowledge corpus: the RAG index must be non-empty for grounded drafts.
    n_chunks = len(retriever.index.docs)
    checks["knowledge"] = {
        "status": "ok" if n_chunks > 0 else "error",
        "chunks": n_chunks,
    }

    # Approval queue: pending is normal; an unbounded backlog is not.
    pending = sum(1 for r in storage.all("reviews") if r.get("status") == "pending")
    checks["approval_queue"] = {
        "status": "ok" if pending < 100 else "degraded",
        "pending_reviews": pending,
    }

    # Outbound deliveries: pending is normal; dead letters are not.
    dead = sum(1 for o in storage.all("outbox")
               if o.get("status") == "dead")
    checks["outbox"] = {
        "status": "ok" if dead == 0 else "degraded",
        "dead_letters": dead,
    }

    # Outbound delivery worker: enabled or explicitly disabled — never "unknown".
    checks["outbound_delivery"] = {
        "status": "ok" if config.OUTBOX_URL else "disabled",
        "target_configured": bool(config.OUTBOX_URL),
    }

    # LLM gateway: mock vs live, plus the routing tiers.
    checks["llm_gateway"] = {
        "status": "ok",
        "mode": config.MODE,
        "light_model": config.LIGHT_MODEL,
        "heavy_model": config.HEAVY_MODEL,
    }

    # Budget posture (mirrors /api/analytics/budget without recomputing spend here).
    budget = budget_snapshot()
    checks["budget"] = budget

    degraded = [k for k, v in checks.items() if v.get("status") == "degraded"]
    errors = [k for k, v in checks.items() if v.get("status") == "error"]
    overall = "error" if errors else ("degraded" if degraded else "ok")
    return {
        "status": overall,
        "mode": config.MODE,
        "checks": checks,
        "degraded": degraded,
        "errors": errors,
    }


def budget_snapshot() -> dict:
    """Budget posture for the health report (kept dependency-free on purpose)."""
    from .budget import budget_status

    try:
        b = budget_status(storage.all("usage"), config.MONTHLY_BUDGET_USD)
    except Exception:  # noqa: BLE001 — health must never 500 because of analytics
        return {"status": "ok", "enabled": False, "note": "budget check unavailable"}
    if not b.get("enabled"):
        return {"status": "ok", "enabled": False}
    return {
        "status": "degraded" if b.get("alert") else "ok",
        "enabled": True,
        "utilization_pct": b.get("utilization_pct"),
        "projected_month_usd": b.get("projected_month_usd"),
        "alert": b.get("alert"),
    }
