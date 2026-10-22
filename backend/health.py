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

    # A collection file that would not parse is data loss, not an empty
    # report: the store quarantined the bytes and recorded the incident, and
    # saying "ok" here would be the last quiet failure worth fearing.
    corruptions = list(getattr(storage, "corruptions", []))
    if corruptions:
        slot = checks.setdefault("storage", {})
        slot["unreadable_files"] = [
            {"collection": c["collection"], "error": c["error"],
             "preserved_at": c.get("quarantined")}
            for c in corruptions]
        # Unwritable storage is the worse news and must not be downgraded to
        # "degraded" just because we also found an unreadable file.
        if slot.get("status") != "error":
            slot["status"] = "degraded"

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

    # Decision receipts: is the deployment's evidence still being signed by a
    # key young enough to be believed to exist? Rotation keeps old receipts
    # verifiable, so nothing forces it — which is exactly why it needs a clock.
    checks["receipts"] = receipts_snapshot()

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


def receipts_snapshot() -> dict:
    """Receipt signing state, including the rotation clock.

    `managed_by` says where the key came from, because that is what decides
    whether this deployment can rotate it at all: an environment key is
    rotated outside, and reporting it as "due" here would be advice nobody
    can act on.
    """
    from . import receipts

    max_age = float(getattr(config, "RECEIPT_KEY_MAX_AGE_DAYS", 365) or 0)
    try:
        age = receipts.key_age_days()
        entries = receipts.published_history() or []
    except Exception as exc:  # noqa: BLE001 — health must not 500 on key state
        return {"status": "error", "error": str(exc)}

    env_managed = bool((config.RECEIPT_KEY or "").strip())
    reported = {
        "signed": len(storage.all("receipts")),
        "active_key_id": entries[-1].get("key_id") if entries else None,
        "published_keys": len(entries),
        "key_age_days": age,
        "managed_by": "environment" if env_managed else "file",
        "rotation_due_after_days": max_age or None,
    }
    if age is None:
        # No history yet, or an environment key we cannot date. Say which, but
        # never call it due: an unknown age is not an overdue one.
        reported["status"] = "ok"
        reported["rotation_due"] = False
        reported["note"] = ("AIOPS_RECEIPT_KEY is managed outside this "
                            "deployment; age is not observable here"
                            if env_managed else "no receipts signed yet")
        return reported

    due = bool(max_age) and age > max_age
    reported["status"] = "degraded" if due else "ok"
    reported["rotation_due"] = due
    if due:
        reported["note"] = (f"the active signing key is {age:.0f} days old "
                            f"(rotation due after {max_age:.0f}); rotate with "
                            f"`python -m scripts.rotate_receipt_key` — old "
                            f"receipts keep verifying against the published "
                            f"history")
    return reported


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
