"""Data retention — the boring operational necessity, made explicit.

JSON-file collections grow forever without this. The policy is simple and
auditable: keep everything newer than N days; beyond that, keep the last
MAX records. Tickets referenced by surviving runs are always kept, so a
run never loses its replay source.

Pure selection logic; the endpoint applies it per collection.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .models import iso_now  # noqa: F401  (used by callers for stamps)

COLLECTION_LIMITS = {
    # collection: (max_age_days, max_records_kept)
    "runs": (90, 5000),
    "tickets": (90, 5000),
    "usage": (90, 20000),
    "deliveries": (30, 5000),
    "feedback": (180, 5000),
}


def parse_ts(value: str | None) -> datetime | None:
    """Best-effort ISO parse; None when missing/unparseable."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def select_for_prune(records: list[dict], max_age_days: int,
                     max_records: int, now: datetime | None = None) -> tuple[list[dict], list[dict]]:
    """Split records into (keep, prune).

    Age cutoff first; then the newest `max_records` of the aged set are
    retained. Unparseable timestamps are kept (never silently destroy
    what can't be dated).
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=max_age_days)

    fresh, aged, unknown = [], [], []
    for r in records:
        ts = parse_ts(r.get("created_at") or r.get("ts"))
        if ts is None:
            unknown.append(r)
        elif ts >= cutoff:
            fresh.append(r)
        else:
            aged.append(r)

    aged.sort(key=lambda r: parse_ts(r.get("created_at") or r.get("ts")), reverse=True)
    keep_aged = aged[:max_records]
    prune_aged = aged[max_records:]
    return fresh + keep_aged + unknown, prune_aged
