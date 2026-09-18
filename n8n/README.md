# n8n Integration

The platform ships an importable n8n workflow that bridges external systems
(email inbox, ticketing tool, web form) into the agentic pipeline.

## Flow

```text
Webhook (new ticket)
   -> POST http://localhost:8000/api/tickets
   -> Switch on disposition
        human_review -> Slack #support-escalations alert
        auto_resolved / other -> no-op (platform already replied)
   -> Respond to source system (ticket_id, disposition, review_id)
```

## Import

1. n8n -> Workflows -> Import from File ->
   `n8n/workflows/ticket_intake_bridge.json`
2. Set your platform URL in the "Run AI Pipeline" node (default
   `http://localhost:8000`).
3. Connect Slack credentials in the "Slack Escalation Alert" node and set the
   target channel.
4. Activate, then POST a test ticket:

```bash
curl -X POST http://localhost:5678/webhook/new-ticket \
  -H "Content-Type: application/json" \
  -d '{"customer_email":"demo@corp.example",
       "subject":"Charged twice",
       "body":"I was charged twice, $49.00, need a refund."}'
```

## Outbound actions

The platform's Side-B actions (`backend/integrations/actions.py`) write to an
outbox (`/api/outbox`). In production, point an n8n schedule/poller at that
endpoint to fan out real refunds/emails/CRM updates - or swap the adapter
bodies for vendor SDK calls. Keeping adapters thin makes either direction a
one-file change.

## Power BI / analytics feed

- `/api/analytics/summary` - KPI snapshot
- `/api/usage` - token + cost per agent call
- `/api/audit` - governance event stream
- `/api/outbox` - executed business actions
- `/api/analyses` - process analyses with ROI

Each maps to a Power BI "Get Data -> Web" connector or a scheduled dataflow.
