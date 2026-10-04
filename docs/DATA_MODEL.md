# Data Model — collections, fields, retention

The platform persists to `data/*.json` through one swappable `Storage`
class (`backend/store.py`): append-only lists per collection, one
`threading.Lock` around read-modify-write, `replace_all` for atomic
prunes. Swapping this file for SQLAlchemy + Postgres is the planned
milestone — no agent, route, or dashboard change should be needed.

## Collections

| Collection | Written by | Shape (key fields) | Notes |
|---|---|---|---|
| `analyses.json` | `POST /api/analyze` | ProcessInput → analyzer output: `process_id`, `score.total`, step `mapping`, `costs`, `assumptions` | Keyed by `process_id` (not `id`) — the workflow designer matches on it |
| `tickets.json` | `pipeline.run_pipeline` | `Ticket`: `id`, `customer_email`, `subject`, `body`, `channel`, `cycle_seconds?` | Replay source for runs; kept when a surviving run references it |
| `runs.json` | `pipeline.run_pipeline` | `PipelineResult`: `id`, `ticket_id`, `disposition`, per-agent trace, `total_cost_usd`, `total_latency_ms`, `cycle_seconds?` | The central fact table for BI; `cycle_seconds` feeds the A/B report |
| `reviews.json` | escalation agent | `ReviewRequest`: `risk_score`, `reason`, `proposed_response`, `status` (`pending/approved/rejected`), `reviewed_by`, `decision_note` | Ground truth for gate precision |
| `usage.json` | LLM gateway | `UsageRecord`: `agent`, `model`, `tokens_in/out`, `cost_usd`, `mode`, `tier` | Powers cost analytics, budget, per-tier routing checks |
| `audit.json` | `integrations.audit` | `{ts, event, payload, correlation_id}` | JSONL-equivalent audit trail; dashboard tail + `/api/audit` |
| `outbox.json` | action agent | outbound action: `action`, `status` (`pending/delivered/dead`), `attempts`, `last_error` | Durable outbound queue; delivered records are never resent |
| `deliveries.json` | outbound worker | delivery attempt ledger: `outbox_id`, `ok`, `attempts`, `status_update` | Signed-delivery audit trail |
| `workflows.json` | `POST /api/workflows/design` | design record: `process_id`, `node_count` (full n8n JSON only via download endpoint) | List stays small; the importable graph is generated on demand |
| `feedback.json` | `POST /api/runs/{id}/feedback` | `Feedback`: `run_id`, `rating` (`up/down`), `correction?`, `reviewer` | Satisfaction analytics + recall proxy for the quality gate |
| `cycle_times.json` | `POST /api/analytics/ab/records` | `{id, ticket_id?, label?, cycle_seconds, cohort:"manual"}` | The manual cohort of the A/B cycle-time report |

## Retention policy

`backend/retention.py::COLLECTION_LIMITS` — `POST /api/admin/prune`
(dry-run by default, `confirm=true` to execute) keeps everything newer
than N days and caps the rest:

| Collection | Max age | Max records |
|---|---|---|
| runs | 90d | 5,000 |
| tickets | 90d | 5,000 |
| usage | 90d | 20,000 |
| deliveries | 30d | 5,000 |
| feedback | 180d | 5,000 |

Unpruned collections (audit, outbox, reviews, cycle_times) are either
append-ledgers you should keep (audit) or naturally bounded. Pruning
never orphans a run: tickets referenced by surviving runs are kept so
replay still works.

## Knowledge corpus

`knowledge_base/*.md` is data, not code: the retriever chunks on `## `
headings (`doc_id` = filename stem). The management API
(`/api/knowledge*`) lists, reads, saves (explicit `overwrite` flag,
traversal-proof slugs) and deletes documents, then rebuilds the index
in place — no restart. Corpus is copied into the pytest tmp dir by the
`isolated_knowledge` fixture, so tests can never mutate the real one.

## Backups

`python -m scripts.backup --retention N` snapshots the data directory
into `AIOPS_BACKUP_DIR` (default `./data_backups`) as
tar-gz archives named `backup-YYYYmmdd-HHMMSS.tar.gz` and prunes to
the newest N (`--list` to inspect). Restore = extract over `data/` —
deliberately a manual, human-supervised step, not an endpoint.

## BI contract

Flat, schema-pinned CSVs for Power BI refresh: `/api/export/runs.csv`,
`/api/export/approvals.csv`, `/api/export/usage.csv` (headers pinned by
tests). `docs/BI_INTEGRATION.md` has the full feed catalog including
the analytics JSON endpoints.
