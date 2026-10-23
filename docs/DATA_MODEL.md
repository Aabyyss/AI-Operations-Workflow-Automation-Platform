# Data Model — collections, fields, retention

The platform persists to `data/*.json` through one swappable `Storage`
class (`backend/store.py`): append-only lists per collection, one
`threading.Lock` around read-modify-write, `replace_all` for atomic
prunes. **Postgres is now the shipped alternative backend** —
`AIOPS_STORAGE=postgres` swaps every collection to one JSONB `documents`
table (`seq`, `collection`, `payload`) behind the identical seven-method
interface; ids, shapes and retention behavior are unchanged
([operations → storage backends](OPERATIONS.md)). JSON stays the
default: the demo, tests and CI never need a database.

## Collections

| Collection | Written by | Shape (key fields) | Notes |
|---|---|---|---|
| `analyses.json` | `POST /api/analyze` | ProcessInput → analyzer output: `process_id`, `score.total`, step `mapping`, `costs`, `assumptions` | Keyed by `process_id` (not `id`) — the workflow designer matches on it |
| `tickets.json` | `pipeline.run_pipeline` | `Ticket`: `id`, `customer_email`, `subject`, `body`, `channel`, `cycle_seconds?` | Replay source for runs; kept when a surviving run references it |
| `runs.json` | `pipeline.run_pipeline` | `PipelineResult`: `id`, `ticket_id`, `disposition`, per-agent trace, `total_cost_usd`, `total_latency_ms`, `cycle_seconds?` | The central fact table for BI; `cycle_seconds` feeds the A/B report |
| `reviews.json` | escalation agent | `ReviewRequest`: `risk_score`, `reason`, `proposed_response`, `status` (`pending/approved/rejected`), `reviewed_by`, `decision_note` | Ground truth for gate precision |
| `usage.json` | LLM gateway | `UsageRecord`: `agent`, `model`, `tokens_in/out`, `cost_usd`, `mode`, `tier` | Powers cost analytics, budget, per-tier routing checks |
| `audit.json` | `integrations.audit` | `{ts, event, payload, correlation_id}` | JSONL-equivalent audit trail; dashboard tail + `/api/audit` |
| `outbox.json` | action agent + outbound worker | base row: `kind`, `ts`, payload; state rows: `status_update:true`, `status` (`pending/delivered/dead`), `attempts`, `last_error`, `not_before?` | Durable outbound queue, append-only: state changes are *added* rows, folded by `outbound.outbox_view()` (newest wins). Delivered records are never resent; `not_before` is the exponential-backoff gate (2/4/8/16 s) |
| `deliveries.json` | outbound worker | delivery attempt ledger: `outbox_id`, `ok`, `attempts`, `status_update` | Signed-delivery audit trail |
| `workflows.json` | `POST /api/workflows/design` | design record: `process_id`, `node_count` (full n8n JSON only via download endpoint) | List stays small; the importable graph is generated on demand |
| `feedback.json` | `POST /api/runs/{id}/feedback` | `Feedback`: `run_id`, `rating` (`up/down`), `correction?`, `reviewer` | Satisfaction analytics + recall proxy for the quality gate |
| `cycle_times.json` | `POST /api/analytics/ab/records` | `{id, ticket_id?, label?, cycle_seconds, cohort:"manual"}` | The manual cohort of the A/B cycle-time report |
| `receipts.json` | `receipts.issue` after every run | `{payload, payload_sha256, signature{alg, key_id, public_key, signature}}`; the payload holds `receipt_id`, `run_id`, `disposition`, `decision`, `quality?`, `response{sha256, chars, grounded_in}?`, `retrieval[]`, `cost_usd`, `actions_taken[]` (addresses redacted), `mode`, `gates`, `chain{index, prev}` | Signed Ed25519 artifact — treat it as evidence, not cache. The reply text is never stored, only its digest. Chained by `payload_sha256`, so order matters and deletion is visible |
| `shadow.json` | `shadow.score` / shadow-mode runs | `{id, created_at, result_id?, ticket{id, category, priority, requested_refund_usd, has_customer_email}, would_escalate, would_auto_resolve, risk_score, confidence, reason, triggers[], proposed_actions[], retrieval[], gates, mode, cost_usd}` | What the gates would have done. Never written to `runs`, so containment/escalation rates are untouched; contains no ticket body or address |

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
| shadow | 30d | 20,000 |

Unpruned collections (audit, outbox, reviews, cycle_times, receipts) are
either append-ledgers you should keep (audit) or naturally bounded.
**`receipts` is exempt on purpose:** a signed receipt is an artifact someone
else holds a copy of, and pruning this deployment's copy breaks the chain an
auditor verifies against. Export it or leave it. Pruning
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
