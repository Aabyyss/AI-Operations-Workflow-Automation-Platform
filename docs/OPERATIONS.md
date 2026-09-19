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

Changing a governance threshold is a **governance decision** — see
[GOVERNANCE.md](GOVERNANCE.md), not a tuning knob.

## 2. Storage layout

JSON persistence under `data/` (git-ignored), behind `backend/store.py`:

```text
data/
  tickets.json       every ticket + full run trace
  reviews.json       human approval queue and decisions
  outbox.json        executed/queued actions (refund, email, CRM, Slack)
  audit.jsonl        append-only audit log — one line per event
```

- `audit.jsonl` is append-only: never edit, rotate by date, ship to your
  log platform if you need retention.
- Backup = copy the directory. Restore = put it back.
- Swapping to Postgres means implementing the same `Storage` interface;
  nothing else in the codebase touches persistence directly.

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

## 4. The n8n bridge (inbound)

`n8n/workflows/ticket_intake_bridge.json` imports a workflow that:

1. Listens for inbound ticket events (email/webhook trigger — wire to your
   helpdesk export or inbox rules).
2. POSTs each ticket to `POST /api/tickets` on the platform.
3. Routes the response: `auto_resolved` → no-op; `human_approval` → Slack
   notification with the review URL.

Import via n8n UI → *Workflows → Import from File*. Set the platform URL
as an n8n variable (`AIOPS_URL`) so dev/prod can differ.

## 5. Monitoring feeds (Power BI / any BI)

| Endpoint | Grain | Typical visual |
|---|---|---|
| `GET /api/analytics/summary` | aggregates: automation rate, cost by agent, disposition mix, latency | KPI cards |
| `GET /api/runs` | one row per run: disposition, cost, tokens, latency, category | trend lines, slicers |
| `GET /api/reviews` | queue state + decisions | reviewer workload |

`BI_INTEGRATION.md` has field-level schema and a sample M query.

## 6. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `live` mode calls fail instantly | missing/invalid `OPENAI_API_KEY` | check `.env`, `config.is_live()` gate |
| Everything escalates to humans | `MIN_RETRIEVAL_SCORE` too high for your corpus | inspect retrieval scores in run traces |
| Auto-resolve on tickets you expected to escalate | threshold env overrides from an old shell | dump effective config at boot |
| Duplicate outbound emails after replay | outbox replayed without dry-run | use the executor's dry-run mode first |
| Dashboard shows stale analytics | analytics computed on demand per run | re-POST a ticket or check `/api/runs` freshness |

## 7. Upgrading / migrating

- Config defaults live in `backend/config.py`; env vars override per deployment.
- The knowledge base is versioned with the repo — policy changes ship as PRs,
  and retrieval tests catch corpus regressions.
- Run `python -m pytest` after any config change: the governance tests pin
  the routing behavior, so a threshold typo fails loudly, not silently.
