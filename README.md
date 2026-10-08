# AI Operations & Workflow Automation Platform

An AI-powered platform that answers the questions businesses actually ask:

> **Should we automate this?** → process analysis, automation scoring, ROI/cost engine
> **How do we run it safely?** → agentic pipeline with RAG, risk gates, human approvals
> **Is it actually working?** → cost, quality, latency, automation-rate monitoring

Two sides in one system — **AI Product Management** (business case) and
**AI Integration** (working pipeline + integrations).

[![CI](https://github.com/Aabyyss/AI-Operations-Workflow-Automation-Platform/actions/workflows/ci.yml/badge.svg)](https://github.com/Aabyyss/AI-Operations-Workflow-Automation-Platform/actions/workflows/ci.yml) ![dispositions](https://img.shields.io/badge/tests-227%2F227-brightgreen) ![mode](https://img.shields.io/badge/default%20mode-mock%20%28no%20API%20keys%29-blue) ![eval](https://img.shields.io/badge/eval-8%2F8%20routing%20accuracy-brightgreen) ![security](https://img.shields.io/badge/security-auth%20·%20roles%20·%20HMAC%20·%20rate%20limit%20%28opt%2Din%29-blue) ![observability](https://img.shields.io/badge/observability-prometheus%20·%20SSE%20·%20health%20·%20replay%20·%20drift-blue) ![version](https://img.shields.io/badge/version-1.5.0-blue)

---

## Quickstart

```bash
pip install -r requirements.txt
cp .env.example .env            # optional — defaults run offline in mock mode
python -m scripts.demo          # end-to-end walkthrough, prints ROI + pipeline results
python -m scripts.eval_quality  # governance eval: routing accuracy + escalation recall
uvicorn backend.main:app --reload
scripts\launch_aiops.cmd       # Windows: one-click start on :8200 + dashboard shortcut
# open http://localhost:8000  -> operator dashboard
```

Runs fully offline in mock mode (deterministic, zero API keys). Set
`AIOPS_MODE=live` + `OPENAI_API_KEY` to swap every LLM call to a real model —
same contracts, same governance, real token costs.

## What it does

### Side A — AI Product Manager
- Describe a process as steps (minutes, repetitive, judgment, money, PII).
- The analyzer maps each step to **automate / assist / human_approval / keep**.
- The ROI engine computes current vs AI-assisted cost, savings, payback,
  first-year ROI, automation rate — every number traceable to an assumption.

### Side B — AI Integration Specialist
A support-ticket pipeline with six agents (LangGraph-orchestrated):

```text
intake -> knowledge(RAG) -> decision(risk engine) -> draft -> quality gate
                                     |                     |
                              low risk: execute      high risk: human review
                              (refund, email, CRM)        approve / reject
```

Governance is code: refunds ≥ $500, 2FA changes, weak retrieval, or low
confidence **always** land in the human approval queue — regardless of what
the model claims. Every decision, action, and token is audit-logged.

## Architecture

```text
dashboard/index.html          operator UI: health strip, quality + A/B panels, knowledge manager
backend/
  main.py                     FastAPI: analyze, tickets, reviews, knowledge, analytics, health
  security.py                 optional API-key gate + role scoping (operator/approver/admin)
  analyzer.py                 process analyzer (Phase 1+2)
  roi.py                      labor + token cost engine
  pipeline.py                 6-agent pipeline + risk gates (source of truth)
  graph.py                    LangGraph orchestration of the same pipeline
  rag.py                      markdown -> chunks -> TF-IDF/live embeddings
  llm.py                      mock / OpenAI-compatible gateway
  mock_ai.py                  deterministic heuristics for offline mode
  store.py                    swappable persistence (11 collections) + `build_storage()`
  pgstore.py                  the same interface on one JSONB table (Postgres, opt-in)
  health.py                   component health report (storage → budget)
  knowledge.py                corpus CRUD: safe slugs, explicit overwrite, auto-reload
  ab_testing.py               ai vs manual cycle-time cohorts (honest-sample gated)
  quality_gate.py             gate precision + recall proxy from outcomes
  cost_compare.py             published market prices vs our measured cost/decision
  one_pager.py                buyer-facing, print-ready rendering of that comparison
  integrations/               CRM, email, billing, Slack + audit log
knowledge_base/               company policies (RAG corpus — editable over the API)
n8n/workflows/                importable ticket-intake bridge workflow
tests/                        227 tests: governance, contracts, security, storage, analytics, dashboard
```

## API tour

```bash
curl -X POST localhost:8000/api/analyze -H "Content-Type: application/json" -d @docs/sample_process.json
curl -X POST localhost:8000/api/tickets -H "Content-Type: application/json" \
  -d '{"customer_email":"a@b.com","subject":"Charged twice","body":"Refund $49 please"}'
curl localhost:8000/api/reviews                          # pending human approvals
curl -X POST localhost:8000/api/reviews/<id>/decision \
  -d '{"reviewer":"amy","note":"APPROVE"}'
curl localhost:8000/api/analytics/summary                # Power BI feed
curl -X POST localhost:8000/api/analytics/cost-comparison \
  -d '{"monthly_volume":1500,"resolution_rate_pct":50}'  # market list prices vs our measured cost
python -m scripts.cost_onepager      # same comparison as a shareable one-pager (marketing/cost-one-pager.html)
```

## Deployment

```bash
docker compose up --build    # API on :8000, n8n on :5678
```

For shared deployments, set the opt-in protections from `.env.example`:
`AIOPS_API_KEY` (API auth), `AIOPS_WEBHOOK_SECRET` (signed n8n intake),
`AIOPS_RATE_LIMIT` (per-IP limiter), `AIOPS_REQUEST_LOG_FILE` (structured
logs). Backups: `python -m scripts.backup --retention 14`.

Prefer a real database? `docker compose --profile postgres up` +
`AIOPS_STORAGE=postgres` runs the identical API on Postgres/JSONB with a
pgvector-ready schema (`python -m scripts.migrate_to_postgres --replace
--verify` moves your JSON data over).

### The learning loop (v1.3.0)

The platform can be questioned, not just watched:

- **Feedback** — thumbs + correction on any run; satisfaction and
  coverage land in `/api/analytics/runs`.
- **Replay** — re-execute any stored ticket and diff disposition and
  response against history before trusting a prompt/policy change.
- **Drift canary** — containment/escalation/failure swings vs the
  preceding window (`/api/analytics/drift`).
- **Budget** — spend tracking with month projection and alerts
  (`AIOPS_MONTHLY_BUDGET_USD`).
- **Prometheus** — `GET /metrics`, zero exporter dependencies.
- **Outbound webhooks** — outbox actions delivered as signed HTTP with
  retry ledger and dead-letter reset (`AIOPS_OUTBOX_URL`).
- **Model routing** — cheap tier for classification, strong tier for
  drafts (`AIOPS_LIGHT_MODEL` / `AIOPS_HEAVY_MODEL`).
- **Retention** — `POST /api/admin/prune` with per-collection caps,
  dry-run first.

### The reliability layer (v1.4.0)

The questions an operator asks at 2am, answered in code:

- **Component health** — `GET /api/health`: storage, corpus, approval
  backlog, dead letters, gateway mode, budget posture — each with a
  machine-readable status, rendered live in the dashboard strip.
- **Knowledge ops** — create/update/delete policy documents over the API
  (traversal-proof slugs, explicit overwrite, auto-reload). No redeploy
  to fix a stale policy.
- **Role-scoped keys** — approver (decisions) and admin (prune) keys
  below the master key; asymmetric on purpose.
- **Honest A/B** — ai-assisted vs manual cycle-time distributions; any
  "% faster" figure is withheld under 5 samples per cohort.
- **Gate quality** — precision from review outcomes, recall proxy from
  feedback thumbs; measured, not re-scored.
- **Windows launcher** — `scripts\launch_aiops.cmd` + Desktop shortcut;
  port 8200, single-instance, logs to `data\launcher.log`.

### Storage choice & tighter scope (v1.5.0)

- **Postgres backend, same interface** — `AIOPS_STORAGE=postgres` puts every
  collection on one JSONB `documents` table behind the identical seven
  methods; insertion order, id lookup and shallow-merge updates behave the
  same. pgvector-ready schema, and an unavailable pgvector extension degrades
  instead of failing. JSON stays the default: the demo, tests and CI never
  need a database.
- **Migration path** — `python -m scripts.migrate_to_postgres --replace
  --verify` copies and then count-checks every collection.
- **Corpus writes need the admin key** — an approver can decide a refund but
  can no longer rewrite the policy the refund is approved against.
- **Dashboard tells the truth about small numbers** — sub-cent token costs no
  longer render as `$0`, and long audit payloads wrap instead of running off
  the card.

## Documentation

| Doc | What's in it |
|---|---|
| [docs/design.md](docs/design.md) | System design: architecture, agents, risk gates, RAG, ROI math |
| [docs/project-plan.md](docs/project-plan.md) | Milestones, roadmap, backlog, risks, definition of done |
| [docs/GOVERNANCE.md](docs/GOVERNANCE.md) | The human-in-the-loop model and its thresholds |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Module walkthrough with request traces |
| [docs/BI_INTEGRATION.md](docs/BI_INTEGRATION.md) | Power BI / analytics feed endpoints and schema |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | Day-2: config, storage, troubleshooting, backup |
| [docs/DATA_MODEL.md](docs/DATA_MODEL.md) | Every collection: writer, shape, retention cap |
| [docs/DECISIONS.md](docs/DECISIONS.md) | Decision log: context → decision → consequence |
| [docs/TESTING.md](docs/TESTING.md) | What the 227 tests pin, fixtures, CI gates |
| [docs/DESIGN_SYSTEM.md](docs/DESIGN_SYSTEM.md) | Dashboard design system: tokens, bright/dark themes, components, accessibility rules |
| [marketing/cost-one-pager.html](marketing/cost-one-pager.html) | Generated buyer one-pager: market list prices vs measured cost, with the crossover rate and where we lose |
| [docs/COMPETITIVE_ANALYSIS.md](docs/COMPETITIVE_ANALYSIS.md) | Market positioning vs Intercom Fin, Zendesk AI, Sierra, Langfuse, n8n — and where we lose |
| [marketing/linkedin-post.md](marketing/linkedin-post.md) | Launch copy, hooks, posting checklist |
| [marketing/demo-dashboard-walkthrough.webm](marketing/demo-dashboard-walkthrough.webm) | Recorded dashboard walkthrough (~55s) + [shot list & narration](marketing/demo-video-shotlist.md) |
| [CHANGELOG.md](CHANGELOG.md) | Release history — what shipped when and why |

## Roadmap

- [x] Quality-gate precision/recall from review outcomes (v1.4.0)
- [x] Per-process A/B: AI-assisted vs manual cycle-time tracking (v1.4.0)
- [x] Postgres + pgvector behind the same `Storage` interface (opt-in)
- [x] Admin-gated knowledge mutations for shared deployments
- [ ] n8n outbound poller executing the outbox against real vendor APIs
- [x] Market cost comparison — published list prices vs measured cost per decision
- [x] Shareable, generated buyer one-pager for that comparison
- [ ] pgvector retrieval cutover behind `AIOPS_RETRIEVAL` (one-file extension point)
- [ ] Signed decision receipts — one verifiable artifact per decision for auditors
