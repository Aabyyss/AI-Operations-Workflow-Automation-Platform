"""Business-system actions.

These are the Side-B integration points: in production each function would
call a real vendor API (HubSpot, Zendesk, SendGrid, Slack). Here they write
to the same stores and audit log, so the pipeline, governance and monitoring
behavior are identical — only the adapter body changes per company.
"""
from __future__ import annotations

import json

from .. import config, store
from ..models import IntakeResult, Ticket, iso_now, new_id
from .audit import audit_log


def _write_outbox(kind: str, payload: dict) -> dict:
    record = {"id": new_id("out"), "ts": iso_now(), "kind": kind, **payload}
    storage.append("outbox", record)
    return record


def queue_refund(ticket: Ticket, intake: IntakeResult, amount: float) -> str:
    """Queue a refund in the billing system."""
    _write_outbox("refund", {
        "ticket_id": ticket.id, "customer": ticket.customer_email,
        "amount_usd": round(amount, 2),
        "reason_code": "REFUND-DUP",
    })
    audit_log("refund_queued", {"ticket_id": ticket.id, "amount_usd": round(amount, 2)})
    return f"refund_queued:${amount:.2f}"


def update_crm(ticket: Ticket, intake: IntakeResult, response_text: str) -> str:
    """Upsert the contact and log the interaction in the CRM."""
    contact = {
        "email": ticket.customer_email,
        "last_category": intake.category.value,
        "last_intent": intake.intent,
        "last_priority": intake.priority.value,
        "notes": response_text[:400],
    }
    _write_outbox("crm_update", {"ticket_id": ticket.id, "contact": contact})
    return f"crm_updated:{ticket.customer_email}"


def send_email(ticket: Ticket, response_text: str) -> str:
    """Send the customer reply via the email provider."""
    _write_outbox("email", {
        "ticket_id": ticket.id,
        "to": ticket.customer_email,
        "subject": f"Re: {ticket.subject}",
        "body": response_text,
    })
    return f"email_sent:{ticket.customer_email}"


def notify_slack(channel: str, text: str) -> str:
    """Post to a Slack channel (used for escalations and urgent tickets)."""
    _write_outbox("slack", {"channel": channel, "text": text})
    return f"slack_posted:{channel}"
