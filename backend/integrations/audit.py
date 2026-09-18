"""Append-only audit log.

Every AI decision and every side effect lands here. In a real enterprise this
feeds compliance reviews and the monitoring dashboard; here it's a JSON store
exposed through /api/audit and exportable to Power BI.
"""
from __future__ import annotations

from ..models import iso_now
from ..store import storage


def audit_log(event: str, payload: dict | None = None) -> dict:
    record = {"ts": iso_now(), "event": event, "payload": payload or {}}
    storage.append("audit", record)
    return record
