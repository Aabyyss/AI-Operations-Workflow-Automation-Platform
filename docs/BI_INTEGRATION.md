# Power BI Integration

The monitoring side ships as clean JSON feeds — each one maps to a Power BI
table via **Get Data → Web** (or a scheduled dataflow pointing at the API).

## Feed → dashboard mapping

| Endpoint                   | Power BI table    | Dashboard visuals                     |
|----------------------------|-------------------|----------------------------------------|
| `/api/analytics/summary`   | `Summary`         | KPI cards: automation rate, pending approvals, LLM spend, est. monthly savings |
| `/api/analytics/runs`      | `RunPerf`         | Latency p50/p95 trend, containment vs human-touch, failure rate |
| `/api/analytics/approvals` | `QueueSLA`        | Queue-aging buckets, oldest pending, human turnaround median, escalation rate |
| `/api/export/runs.csv`     | `Runs` (flat)     | Disposition mix, latency distribution, cost per run — **recommended for scheduled refresh (schema pinned by tests)** |
| `/api/export/approvals.csv`| `Approvals` (flat)| Reviewer workload, decision mix over time |
| `/api/export/usage.csv`    | `Usage` (flat)    | Cost by agent, tokens over time, cost-per-ticket trend |
| `/api/usage`               | `Usage` (JSON)    | Same data, ad-hoc exploration |
| `/api/audit`               | `Audit`           | Event stream, escalations/day |
| `/api/outbox`              | `Outbox`          | Executed actions by type, refund volume by day |
| `/api/analyses`            | `Analyses`        | ROI per process, payback comparison, automation-score ranking |

## Suggested report pages

1. **Executive ROI** — monthly savings, payback months, automation rate,
   tickets processed. (Analyses + Summary)
2. **AI Quality** — auto-resolve vs escalated vs approved-executed mix,
   quality-gate failure reasons, human edit rate. (Runs + Audit)
3. **Cost & Tokens** — spend by agent, by model, trend by day; projected
   monthly spend at current volume. (Usage)
4. **Operations** — queue-aging buckets, human turnaround median, escalation
   rate, containment rate. (Approvals + RunPerf + Audit)
5. **Learning loop** *(v1.3.0)* — satisfaction % from `/api/analytics/runs`
   (`feedback` block: thumbs ratio, correction count, coverage), drift-signal
   deltas from `/api/analytics/drift`, budget utilization and projection from
   `/api/analytics/budget`, delivery outcomes from `/api/deliveries`.

## Refresh

For scheduled refresh, prefer the **CSV exports** — their columns are
pinned by `tests/test_exports.py`, so a breaking schema change fails CI
before it can break a dashboard. `Get Data → Web` on
`/api/export/runs.csv` (or a dataflow pointing at the API) refreshes on
any cadence; the raw stores update on every request.

For infrastructure monitoring rather than BI, point Prometheus at
`GET /metrics` (text exposition, v0.0.4) — no exporter sidecar needed.
