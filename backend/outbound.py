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

Backoff is per-item and self-clocked through ``not_before``, so a record
keeps its own cooling-off window across multiple ``deliver_pending``
polls. The vanity constant ``BASE_DELAY_S`` is kept only for
documentation.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

from . import config
from .integrations.audit import audit_log
from .store import storage
from .webhook import sign

MAX_ATTEMPTS = 5
BASE_DELAY_S = 2.0  # exponential: 2, 4, 8, 16 seconds (doc-only; backoff is in _backoff_s)


def _utcnow() -> datetime:
    """The clock retries schedule against (single seam so tests can move it)."""
    return datetime.now(timezone.utc)


def _backoff_s(attempts: int) -> float:
    """Delay before the next try, doubling per failed attempt.

    Without this the retry budget is spent at *cron* frequency, not over
    time: a deliver endpoint polled every second marks a vendor blip of
    five seconds as five failed attempts and dead-letters the queue.
    """
    return BASE_DELAY_S * (2 ** max(0, attempts - 1))


def _due(item: dict, now: datetime) -> bool:
    """True unless the item is still inside its backoff window.

    A record without not_before (or an unparseable one) is due: old ledgers
    written before this field existed must keep flowing.
    """
    raw = item.get("not_before")
    if not raw:
        return True
    try:
        return datetime.fromisoformat(raw) <= now
    except ValueError:
        return True


def deliver_pending(max_items: int = 25) -> dict:
    """Attempt delivery of pending outbox records. Returns a summary.

    Reads the *folded* view so state from earlier passes (delivered, dead)
    is respected — the raw records are append-only and never change.
    """
    if not config.OUTBOX_URL:
        return {"attempted": 0, "delivered": 0, "deferred": 0, "dead": 0,
                "waiting": 0, "reason": "AIOPS_OUTBOX_URL not configured"}

    pending = [o for o in outbox_view()
               if o.get("status", "pending") == "pending"]
    now = _utcnow()
    ready = [o for o in pending if _due(o, now)]
    summary = {"attempted": 0, "delivered": 0, "deferred": 0, "dead": 0,
               # Still cooling down — reported so a flush that "did nothing"
               # never looks like the queue vanished.
               "waiting": len(pending) - len(ready)}

    for item in ready[:max_items]:
        summary["attempted"] += 1
        ok, attempts = _attempt(item)
        if ok:
            summary["delivered"] += 1
        elif attempts >= MAX_ATTEMPTS:
            summary["dead"] += 1
            _set_outbox_status(item["id"], "dead", attempts=attempts,
                               last_error="retry budget exhausted")
        else:
            summary["deferred"] += 1
    return summary


def _attempt(item: dict) -> tuple[bool, int]:
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
    now = _utcnow()
    storage.append("deliveries", {
        "id": f"dlv_{item['id']}",
        "outbox_id": item["id"], "kind": item.get("kind"),
        "attempt": attempts, "status_code": status,
        "ok": delivered, "error": error or None, "ts": now.isoformat(),
    })
    _set_outbox_status(item["id"], "delivered" if delivered else "pending",
                       attempts=attempts, last_error=error or None,
                       not_before=None if delivered
                       else (now + timedelta(seconds=_backoff_s(attempts))).isoformat())
    audit_log("outbox_delivered" if delivered else "outbox_retry_scheduled",
              {"outbox_id": item["id"], "attempt": attempts, "status_code": status})
    return delivered, attempts


def _set_outbox_status(outbox_id: str, status: str, attempts: int,
                       last_error: str | None, not_before: str | None = None) -> None:
    """Outbox entries are append-only; mark state via a status update record.

    `not_before` is always written (null when due now) so a later update
    *clears* the previous backoff instead of the fold keeping it alive.
    """
    from .models import iso_now
    storage.append("outbox", {
        "id": outbox_id, "status_update": True, "status": status,
        "attempts": attempts, "last_error": last_error,
        "not_before": not_before, "ts": iso_now(),
    })


def retry(outbox_id: str) -> dict | None:
    """Reset a dead or failing record to pending with a fresh budget.

    Operational calm-after-the-storm tool: the vendor endpoint was down,
    the queue filled with dead letters, the endpoint is back — reset and
    flush. Deliberately audit-visible (a status update record, like every
    other transition).
    """
    targets = [o for o in outbox_view()
               if o["id"] == outbox_id and o.get("status") != "delivered"]
    if not targets:
        return None
    _set_outbox_status(outbox_id, "pending", attempts=0, last_error=None)
    return outbox_view_dict(outbox_id)


def outbox_view_dict(outbox_id: str) -> dict | None:
    return next((o for o in outbox_view() if o["id"] == outbox_id), None)


def outbox_view() -> list[dict]:
    """Outbox records with status updates folded in, newest first."""
    state: dict[str, dict] = {}
    for rec in storage.all("outbox"):
        if rec.get("status_update"):
            base = {"id": rec["id"], **state.get(rec["id"], {})}
            base.update({k: rec[k] for k in
                         ("status", "attempts", "last_error", "not_before")
                         if k in rec})
            state[rec["id"]] = base
        else:
            state[rec["id"]] = {**rec, "status": rec.get("status", "pending"), "attempts": 0}
    return sorted(state.values(), key=lambda r: r.get("ts", ""), reverse=True)


def outbox_dead_count(outbox: list[dict] | None = None) -> int:
    """How many outbox items currently carry status=dead (folded view)."""
    return sum(1 for o in (outbox or outbox_view()) if o.get("status") == "dead")


def outbox_pending_count(outbox: list[dict] | None = None) -> int:
    """How many outbox items currently carry status=pending (folded view).

    Exportable so the Prometheus and health consumers share one definition
    instead of each duplicating the fold logic.
    """
    return sum(1 for o in (outbox or outbox_view()) if o.get("status", "pending") == "pending")
