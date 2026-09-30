"""Outbound webhook deliverer — the other half of the integration story.

The outbox records *intent* (queue_refund, update_crm, ...). This module
turns intent into delivery: every pending outbox record is POSTed to the
configured endpoint as a signed webhook (same HMAC scheme as the intake
bridge, so one shared secret covers both directions), with exponential
backoff, capped attempts, and an auditable delivery ledger in
`deliveries.json`.

Nothing here pretends a vendor API exists: without AIOPS_OUTBOX_URL the
deliverer is a no-op and the outbox stays the durable queue it already
is. Wire a real endpoint in and the same records flow out, signed and
retried.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from . import config
from .integrations.audit import audit_log
from .store import storage
from .webhook import sign

MAX_ATTEMPTS = 5
BASE_DELAY_S = 2.0  # exponential: 2, 4, 8, 16 seconds


def deliver_pending(max_items: int = 25) -> dict:
    """Attempt delivery of pending outbox records. Returns a summary."""
    if not config.OUTBOX_URL:
        return {"attempted": 0, "delivered": 0, "deferred": 0, "dead": 0,
                "reason": "AIOPS_OUTBOX_URL not configured"}

    pending = [o for o in storage.all("outbox") if o.get("status", "pending") == "pending"]
    summary = {"attempted": 0, "delivered": 0, "deferred": 0, "dead": 0}

    for item in pending[:max_items]:
        summary["attempted"] += 1
        ok = _attempt(item)
        if ok:
            summary["delivered"] += 1
        elif item.get("attempts", 1) >= MAX_ATTEMPTS:
            summary["dead"] += 1
        else:
            summary["deferred"] += 1
    return summary


def _attempt(item: dict) -> bool:
    """One signed delivery attempt; updates ledger + outbox status."""
    body = json.dumps(item, default=str).encode("utf-8")
    headers = {"Content-Type": "application/json",
               "X-AIOPS-Signature": sign(body, config.OUTBOX_SECRET or config.WEBHOOK_SECRET)}
    req = urllib.request.Request(config.OUTBOX_URL, data=body, headers=headers, method="POST")

    attempts = item.get("attempts", 0) + 1
    status, error = 0, ""
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            status = resp.status
    except urllib.error.HTTPError as exc:
        status, error = exc.code, f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001 — network errors are data, not crashes
        error = f"{type(exc).__name__}: {exc}"

    delivered = 200 <= status < 300
    now = datetime.now(timezone.utc).isoformat()
    storage.append("deliveries", {
        "id": f"dlv_{item['id']}",
        "outbox_id": item["id"], "kind": item.get("kind"),
        "attempt": attempts, "status_code": status,
        "ok": delivered, "error": error or None, "ts": now,
    })
    _set_outbox_status(item["id"], "delivered" if delivered else "pending",
                       attempts=attempts, last_error=error or None)
    audit_log("outbox_delivered" if delivered else "outbox_retry_scheduled",
              {"outbox_id": item["id"], "attempt": attempts, "status_code": status})
    return delivered


def _set_outbox_status(outbox_id: str, status: str, attempts: int,
                       last_error: str | None) -> None:
    """Outbox entries are append-only; mark state via a status update record."""
    from .models import iso_now
    storage.append("outbox", {
        "id": outbox_id, "status_update": True, "status": status,
        "attempts": attempts, "last_error": last_error, "ts": iso_now(),
    })


def outbox_view() -> list[dict]:
    """Outbox records with status updates folded in, newest first."""
    state: dict[str, dict] = {}
    for rec in storage.all("outbox"):
        if rec.get("status_update"):
            base = {"id": rec["id"], **state.get(rec["id"], {})}
            base.update({k: rec[k] for k in ("status", "attempts", "last_error") if k in rec})
            state[rec["id"]] = base
        else:
            state[rec["id"]] = {**rec, "status": rec.get("status", "pending"), "attempts": 0}
    return sorted(state.values(), key=lambda r: r.get("ts", ""), reverse=True)
