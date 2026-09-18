# Power BI Integration

The monitoring side ships as clean JSON feeds — each one maps to a Power BI
table via **Get Data → Web** (or a scheduled dataflow pointing at the API).

## Feed → dashboard mapping

| Endpoint                   | Power BI table    | Dashboard visuals                     |
|----------------------------|-------------------|----------------------------------------|
| `/api/analytics/summary`   | `Summary`         | KPI cards: automation rate, pending approvals, LLM spend, est. monthly savings |
| `/api/usage`               | `Usage`           | Cost by agent (bar), tokens over time (line), cost per ticket trend |
| `/api/audit`               | `Audit`           | Event stream, escalations/day, approval turnaround |
| `/api/outbox`              | `Outbox`          | Executed actions by type, refund volume by day |
| `/api/analyses`            | `Analyses`        | ROI per process, payback comparison, automation-score ranking |
| `/api/runs` (via tickets)  | `Runs`            | Disposition mix, latency distribution, quality-gate failures |

## Suggested report pages

1. **Executive ROI** — monthly savings, payback months, automation rate,
   tickets processed. (Analyses + Summary)
2. **AI Quality** — auto-resolve vs escalated vs approved-executed mix,
   quality-gate failure reasons, human edit rate. (Runs + Audit)
3. **Cost & Tokens** — spend by agent, by model, trend by day; projected
   monthly spend at current volume. (Usage)
4. **Operations** — pending review queue aging, escalation reasons, audit
   stream. (Audit + Reviews)

## Refresh

Point a scheduled refresh (or an n8n schedule → Power BI push) at
`/api/analytics/summary` for near-real-time KPIs; the raw stores refresh on
every request, so any cadence works.
