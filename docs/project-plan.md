# Project Plan — AI Operations & Workflow Automation Platform

Portfolio project #2: demonstrating **AI Product Management** (business case,
ROI, governance) and **AI Integration** (agents, RAG, integrations, deployment)
in one working system.

---

## 1. Goals and non-goals

**Goals**
- Answer "should we automate this?" with a defensible business case, not vibes.
- Run a real support-ticket pipeline: RAG-grounded drafts, deterministic risk
  gates, human approvals, audited actions.
- Measure everything that matters to a buyer: automation rate, cost per ticket,
  latency, escalation mix, token spend.
- Run offline by default (mock mode) — no API keys to try, CI, or grade it.

**Non-goals (v1)**
- Multi-tenant SaaS; this is a deployable single-org system.
- Real vendor API calls (CRM/email) — the outbox + n8n bridge is the seam.
- Kubernetes; Docker Compose with API + n8n is the deployment story.

## 2. Milestones

| # | Milestone | Status | Proof it's done |
|---|---|---|---|
| 1 | Typed contracts + storage + mock/live LLM gateway | ✅ | everything else imports `models.py` |
| 2 | ROI engine (labor + token economics) | ✅ | unit-tested math, per-assumption output |
| 3 | Process analyzer → business case artifact | ✅ | `POST /api/analyze` end-to-end |
| 4 | RAG over `knowledge_base/` | ✅ | ranking tests, grounding contract |
| 5 | 6-agent pipeline + deterministic risk gates | ✅ | governance tests (money/security/weak-RAG) |
| 6 | Human approval loop (queue → decide → execute) | ✅ | API test: reject → no customer email |
| 7 | Integrations + audit log | ✅ | JSONL audit with correlation IDs |
| 8 | FastAPI + operator dashboard | ✅ | dashboard serves, analytics populate |
| 9 | n8n bridge + Docker Compose | ✅ | importable workflow JSON, `compose up` |
| 10 | Tests + CI (3.11–3.13) | ✅ | 46/46 green in GitHub Actions |
| 11 | Postgres + pgvector behind `Storage` | ⬜ next | same interface, migration script |
| 12 | Outbound n8n executor (outbox → real vendors) | ⬜ | dry-run replay mode |
| 13 | Quality eval harness (labeled ticket set) | ✅ | `python -m scripts.eval_quality`: 8/8 routing accuracy, 100% escalation recall, CI `eval-quality` job |
| 14 | Workflow designer (Analysis → importable n8n graph) | ✅ | `POST /api/workflows/design`, deterministic JSON, download endpoint |
| 15 | Approval-queue SLA + run performance analytics | ✅ | `/api/analytics/approvals`, `/api/analytics/runs` with p50/p95, containment, aging |
| 16 | Schema-pinned CSV exports for BI refresh | ✅ | `/api/export/{runs,approvals,usage}.csv`, columns pinned in CI |
| 17 | Batch ingestion with per-item isolation | ✅ | `POST /api/tickets/batch` (≤100), aggregated summary |

## 3. Development roadmap (phases)

```text
Phase 1  Process analyzer        input → step mapping → AI opportunities
Phase 2  ROI engine              current vs AI cost, savings, payback, ROI
Phase 3  RAG                     policies → chunks → retrieval
Phase 4  Agentic workflow        intake → knowledge → decide → draft → quality → act
Phase 5  Integrations            CRM, email, billing, Slack + audit
Phase 6  Human approval          risk → queue → approve/reject → execute
Phase 7  Monitoring              cost, quality, latency, escalation analytics
Phase 8  Deployment & BI         Docker Compose + n8n, Power BI feeds
```

Phases 1–8 are complete; the roadmap items below extend the same skeleton.

## 4. Backlog (post-v1)

- **Postgres + pgvector** — swap JSON store for real DB behind `Storage`;
  embeddings table for live-mode retrieval.
- **Outbound n8n executor** — poll outbox, execute against real Gmail/Slack/HubSpot
  APIs, replay-safe dry-run mode.
- **Evaluation harness** — ✅ shipped: labeled set with routing accuracy,
  escalation recall, cost per ticket; runs in CI as a regression gate.
  Extend the label set as new ticket families appear.
- **A/B cycle-time tracking** — tag tickets AI-assisted vs manual, compare
  resolution time in analytics.
- **Auth + roles** — operator vs approver vs admin for the dashboard.

## 5. Risks and mitigations

| Risk | Mitigation |
|---|---|
| LLM quality regresses with a live model | Deterministic gates stay; `eval-quality` CI job gates every push |
| Scope creep into "enterprise everything" | Non-goals above; one vertical (support tickets) done deeply |
| Demo only works with API keys | Mock mode is the default; CI proves the whole pipeline offline |
| Numbers look made up | ROI emits its assumptions next to every figure |
| Human queue becomes a bottleneck | Queue SLA endpoint tracks aging buckets, turnaround, and escalation rate from day one |

## 6. Definition of done (per feature)

1. Typed contracts updated first (`models.py`).
2. Implementation behind an interface (LLM, storage, retrieval all swappable).
3. Tests cover the **governance behavior**, not just the happy path.
4. Audit trail records what happened and why.
5. Dashboard/analytics can see the new signal.

## 7. Weekly cadence (how this was actually built)

- Sessions 1–2: contracts, storage, gateway, ROI engine (foundation).
- Session 3: RAG + knowledge base + analyzer.
- Session 4: agent pipeline, risk gates, integrations, approvals.
- Session 5: dashboard, demo script, tests to 25/25 green.
- Session 6: n8n bridge, Docker, docs, CI — then froze scope.
