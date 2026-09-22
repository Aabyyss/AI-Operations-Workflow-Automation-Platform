# AI Operations & Workflow Automation Platform

An AI-powered platform that answers the questions businesses actually ask:

> **Should we automate this?** → process analysis, automation scoring, ROI/cost engine
> **How do we run it safely?** → agentic pipeline with RAG, risk gates, human approvals
> **Is it actually working?** → cost, quality, latency, automation-rate monitoring

Two sides in one system — **AI Product Management** (business case) and
**AI Integration** (working pipeline + integrations).

[![CI](https://github.com/Aabyyss/AI-Operations-Workflow-Automation-Platform/actions/workflows/ci.yml/badge.svg)](https://github.com/Aabyyss/AI-Operations-Workflow-Automation-Platform/actions/workflows/ci.yml) ![dispositions](https://img.shields.io/badge/tests-81%2F81-brightgreen) ![mode](https://img.shields.io/badge/default%20mode-mock%20%28no%20API%20keys%29-blue) ![eval](https://img.shields.io/badge/eval-8%2F8%20routing%20accuracy-brightgreen) ![security](https://img.shields.io/badge/security-auth%20·%20HMAC%20·%20rate%20limit%20%28opt%2Din%29-blue) ![version](https://img.shields.io/badge/version-1.2.0-blue)

---

## Quickstart

```bash
pip install -r requirements.txt
cp .env.example .env            # optional — defaults run offline in mock mode
python -m scripts.demo          # end-to-end walkthrough, prints ROI + pipeline results
python -m scripts.eval_quality  # governance eval: routing accuracy + escalation recall
uvicorn backend.main:app --reload
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
dashboard/index.html          operator UI (served by the API)
backend/
  main.py                     FastAPI: analyze, tickets, reviews, analytics
  analyzer.py                 process analyzer (Phase 1+2)
  roi.py                      labor + token cost engine
  pipeline.py                 6-agent pipeline + risk gates (source of truth)
  graph.py                    LangGraph orchestration of the same pipeline
  rag.py                      markdown -> chunks -> TF-IDF/live embeddings
  llm.py                      mock / OpenAI-compatible gateway
  mock_ai.py                  deterministic heuristics for offline mode
  store.py                    swappable JSON persistence
  integrations/               CRM, email, billing, Slack + audit log
knowledge_base/               company policies (RAG corpus)
n8n/workflows/                importable ticket-intake bridge workflow
tests/                        25 tests: ROI math, RAG, governance, API
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
```

## Deployment

```bash
docker compose up --build    # API on :8000, n8n on :5678
```

For shared deployments, set the opt-in protections from `.env.example`:
`AIOPS_API_KEY` (API auth), `AIOPS_WEBHOOK_SECRET` (signed n8n intake),
`AIOPS_RATE_LIMIT` (per-IP limiter), `AIOPS_REQUEST_LOG_FILE` (structured
logs). Backups: `python -m scripts.backup --retention 14`.

## Documentation

| Doc | What's in it |
|---|---|
| [docs/design.md](docs/design.md) | System design: architecture, agents, risk gates, RAG, ROI math |
| [docs/project-plan.md](docs/project-plan.md) | Milestones, roadmap, backlog, risks, definition of done |
| [docs/GOVERNANCE.md](docs/GOVERNANCE.md) | The human-in-the-loop model and its thresholds |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Module walkthrough with request traces |
| [docs/BI_INTEGRATION.md](docs/BI_INTEGRATION.md) | Power BI / analytics feed endpoints and schema |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | Day-2: config, storage, troubleshooting, backup |
| [CHANGELOG.md](CHANGELOG.md) | Release history — what shipped when and why |

## Roadmap

- [ ] Postgres + pgvector behind the same `Storage` interface
- [ ] n8n outbound poller executing the outbox against real vendor APIs
- [ ] Quality-gate precision/recall dashboard from review outcomes
- [ ] Per-process A/B: AI-assisted vs manual cycle-time tracking
