# Operations Guide — day-2 running of the platform

Everything an operator needs after `git clone`: configuration, storage
layout, the n8n bridge, monitoring feeds, and troubleshooting.

---

## 1. Configuration

All runtime settings come from environment variables (copy `.env.example`
to `.env`). Defaults are safe: **mock mode, offline, no keys**.

| Variable | Default | Meaning |
|---|---|---|
| `AIOPS_MODE` | `mock` | `mock` = deterministic pipeline; `live` = real LLM calls |
| `OPENAI_API_KEY` | — | required when `AIOPS_MODE=live` |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | any OpenAI-compatible endpoint (Azure, OpenRouter, vLLM) |
| `AIOPS_CHAT_MODEL` | `gpt-4o-mini` | chat model for live mode |
| `AIOPS_EMBED_MODEL` | `text-embedding-3-small` | embedding model for live retrieval |
| `AIOPS_AUTO_EXECUTE_MAX_RISK` | `0.30` | risk ≤ this runs without a human |
| `AIOPS_MONETARY_APPROVAL_LIMIT_USD` | `500` | money actions above this always need approval |
| `AIOPS_MIN_RETRIEVAL_SCORE` | `0.15` | below this, retrieval is "weak" → escalate |
| `AIOPS_MIN_AUTO_CONFIDENCE` | `0.75` | blended confidence floor for auto-execution |
| `AIOPS_API_KEY` | — | master key; enables auth, grants the **operator** role |
| `AIOPS_APPROVER_KEY` | — | role-scoped key: read + review decisions |
| `AIOPS_ADMIN_KEY` | — | role-scoped key: admin mutations (prune) — never approval decisions |

Changing a governance threshold is a **governance decision** — see
[GOVERNANCE.md](GOVERNANCE.md), not a tuning knob.

## 2. Storage layout

JSON persistence under `data/` (git-ignored), behind `backend/store.py`:

```text
data/
  tickets.json       every ticket + full run trace
  runs.json          one record per pipeline execution
  reviews.json       human approval queue and decisions
  usage.json         per-LLM-call token + cost ledger
  outbox.json        executed/queued actions (refund, email, CRM, Slack)
  deliveries.json    outbound delivery attempts ledger (v1.3.0)
  feedback.json      operator thumbs/corrections per run (v1.3.0)
  workflows.json     generated n8n workflow records
  analyses.json      stored process analyses
  cycle_times.json   manual-cohort cycle-time records for A/B (v1.4.0)
  audit.jsonl        append-only audit log — one line per event
```

- `audit.jsonl` is append-only: never edit, rotate by date, ship to your
  log platform if you need retention.
- Backup = copy the directory. Restore = put it back.
- Swapping to Postgres means implementing the same `Storage` interface;
  nothing else in the codebase touches persistence directly.
- Every collection's writer, shape and retention cap: [DATA_MODEL.md](DATA_MODEL.md).

### 2.1 Storage backends: JSON (default) or Postgres

JSON files need nothing and remain the default. For shared or
production deployments, the same `Storage` interface runs on
Postgres/JSONB — no agent, route or dashboard changes:

```bash
pip install -r requirements-postgres.txt
export AIOPS_STORAGE=postgres
export AIOPS_DATABASE_URL=postgresql://aiops:aiops@localhost:5432/aiops
python -m scripts.migrate_to_postgres --replace --verify   # one-shot copy + count check
```

`docker compose --profile postgres up` provisions a pgvector-ready
instance (`pgvector/pgvector:pg16`); the API image already ships the
driver and the migrator. Schema bootstrap also creates the pgvector
extension and an `embeddings` table so live-mode retrieval can cut over
without another migration — the retriever swap itself remains the
one-file extension point (`rag.py`). Swapping back to JSON is the same
two env vars. `/api/health` reports the backend's record counts either
way.

## 3. Running the API

```bash
uvicorn backend.main:app --host 0.0.0.0 --port 8000
# dashboard:   http://localhost:8000/
# health:      http://localhost:8000/health
# OpenAPI docs at /docs (FastAPI default)
```

Demo fixture (no data setup needed):

```bash
curl -X POST localhost:8000/api/tickets/demo
```

### 3.1 One-click launch (Windows)

`scripts\launch_aiops.cmd` starts the API on **:8200** and opens the
dashboard in the default browser. It never starts a second instance — an
existing listener on :8200 just reopens the dashboard — waits up to 20 s
for `/health`, and logs to `data\launcher.log`. To install the Desktop
shortcut ("AI Ops Platform", idempotent — rerunning it repairs the `.lnk`):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\create_desktop_shortcut.ps1
```

The launcher deliberately uses :8200 so it can never collide with a
developer's own `uvicorn` on :8000.

## 4. The n8n bridge

### 4.1 Inbound (prebuilt)

`n8n/workflows/ticket_intake_bridge.json` imports a workflow that:

1. Listens for inbound ticket events (email/webhook trigger — wire to your
   helpdesk export or inbox rules).
2. POSTs each ticket to `POST /api/tickets` on the platform (or
   `POST /api/tickets/batch` for fan-in of up to 100 items).
3. Routes the response: `auto_resolved` → no-op; `human_review` → Slack
   notification with the review ID.

Import via n8n UI → *Workflows → Import from File*. Set the platform URL
as an n8n variable (`AIOPS_URL`) so dev/prod can differ.

### 4.2 Generated per process (workflow designer)

The designer turns any stored Analysis into an importable workflow:

```bash
curl -X POST localhost:8000/api/workflows/design \
  -H 'Content-Type: application/json' \
  -d '{"process_id": "<from /api/analyses>"}'
# then: GET /api/workflows/{workflow_id}/download  -> n8n → Import from File
```

The generated graph mirrors the governance model — intake webhook →
agentic pipeline → IF disposition = `human_review` (Slack escalation) else
reply, plus an hourly monitoring digest and a sticky note carrying the
per-step AI mapping. Generation is deterministic (no LLM), so the artifact
is reviewable in a PR like any other integration change.

## 5. Monitoring feeds (Power BI / any BI)

| Endpoint | Grain | Typical visual |
|---|---|---|
| `GET /api/analytics/summary` | aggregates: automation rate, cost by agent, disposition mix, latency | KPI cards |
| `GET /api/analytics/runs` | per-run performance: latency p50/p95, failure, containment, human-touch, cost/run | service-level trend lines |
| `GET /api/analytics/approvals` | queue SLA: aging buckets, oldest pending, human turnaround, escalation rate | queue-aging bars, reviewer workload |
| `GET /api/export/runs.csv` | flat, schema-pinned CSV of runs | Power BI scheduled refresh |
| `GET /api/export/approvals.csv` | flat CSV of review decisions | Power BI scheduled refresh |
| `GET /api/export/usage.csv` | flat CSV of per-call LLM usage | cost attribution |
| `GET /api/runs` | one row per run: disposition, cost, tokens, latency, category | ad-hoc JSON exploration |
| `GET /api/reviews` | queue state + decisions | reviewer workload |

`BI_INTEGRATION.md` has field-level schema and a sample M query. The CSV
schemas are pinned by tests (`tests/test_exports.py`) — a breaking column
change fails CI instead of silently breaking a scheduled refresh.

### 5.1 v1.3.0 operational endpoints

| Endpoint | Purpose |
|---|---|
| `GET /metrics` | Prometheus scrape: throughput, latency quantiles, cost, queue depth, feedback |
| `GET /api/analytics/budget` | spend vs `AIOPS_MONTHLY_BUDGET_USD`, 120% projection alert |
| `GET /api/analytics/drift` | recent-vs-baseline containment/escalation/failure swing |
| `POST /api/runs/{id}/replay` | re-execute a stored ticket, diff disposition + response |
| `GET /api/runs/stream` | SSE tail of new runs (backfills recent history on connect) |
| `POST /api/runs/{id}/feedback` · `GET /api/feedback` | operator thumbs + corrections |
| `POST /api/outbox/deliver` | flush queued actions as signed webhooks (`AIOPS_OUTBOX_URL`) |
| `POST /api/outbox/{id}/retry` | reset a dead delivery with a fresh retry budget |
| `GET /api/deliveries` | audit-grade ledger of every delivery attempt |
| `POST /api/admin/prune` | retention dry-run; `?confirm=true` executes (see §7) |
| Model routing | `AIOPS_LIGHT_MODEL` (intake/quality) vs `AIOPS_HEAVY_MODEL` (drafts) — inert until set |

### 5.2 v1.4.0 operational endpoints

| Endpoint | Purpose |
|---|---|
| `GET /api/health` | component report: storage record counts, corpus chunks, queue backlog (≥ 100 pending → degraded), outbox dead letters, delivery config, gateway mode/tiers, budget posture. Needs a key when auth is on — use `/health` or `/ready` for probes |
| `GET /api/knowledge` | corpus inventory: id, title, chunk count, size, last modified |
| `POST /api/knowledge` | create/update a policy document; replacing requires explicit `overwrite` (409 otherwise); index reloads before responding |
| `GET /api/knowledge/{doc_id}` · `DELETE /api/knowledge/{doc_id}` | read / remove a document (index reloads immediately) |
| `POST /api/analytics/ab/records` | stamp a manual-cohort cycle time (ticket, label, `cycle_seconds`) |
| `GET /api/analytics/ab` | ai-assisted vs manual cycle-time distributions; `%` withheld under 5 samples per cohort |
| `GET /api/analytics/quality` | gate precision (rejected / decided escalations) + recall proxy from thumbs-down auto-resolutions |
| `GET /api/analytics/market-plans` | dated list-price snapshot for the AI-support vendor presets, each with its source — inspect or override before modelling |
| `POST /api/analytics/cost-comparison` | model published market pricing at your volume against our **measured** cost per decision: per-plan monthly/annual/per-ticket cost, the resolution rate above which each plan overtakes the pipeline, the plans that beat us, plus assumptions and caveats. Read-only and offline; audited as `cost_comparison_modelled` |

**Role scoping.** Review decisions require the operator or approver key;
`/api/admin/prune` and knowledge mutations (`POST`/`DELETE /api/knowledge`)
require the operator or admin key — corpus writes shape what the AI tells
customers, so they are admin-level. Every other route accepts any
configured role. With auth disabled everything stays open — local dev is
unchanged.

## 6. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `live` mode calls fail instantly | missing/invalid `OPENAI_API_KEY` | check `.env`, `config.is_live()` gate |
| Everything escalates to humans | `MIN_RETRIEVAL_SCORE` too high for your corpus | inspect retrieval scores in run traces |
| Auto-resolve on tickets you expected to escalate | threshold env overrides from an old shell | dump effective config at boot |
| Duplicate outbound emails after replay | outbox replayed without dry-run | use the executor's dry-run mode first |
| Dashboard shows stale analytics | analytics computed on demand per run | re-POST a ticket or check `/api/runs` freshness |
| Dashboard panels stuck on "Loading…" | dashboard script parse error (one shipped in v1.3.0) | fixed in v1.4.0 — hard-refresh the page (Ctrl+F5) |
| `403 requires role: ...` | presented key lacks the route's role | use the master key or the matching scoped key |
| Edited a `.md` by hand, drafts unchanged | retriever still holds the old index | `POST /api/knowledge/reload` (API-created docs reload automatically) |

## 7. Upgrading / migrating

- Config defaults live in `backend/config.py`; env vars override per deployment.
- The knowledge base is versioned with the repo — policy changes ship as PRs,
  and retrieval tests catch corpus regressions.
- Run `python -m pytest` after any config change: the governance tests pin
  the routing behavior, so a threshold typo fails loudly, not silently.
