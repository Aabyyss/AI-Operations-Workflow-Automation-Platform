# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [1.1.0] — 2026-09-21

### Added
- **Workflow designer** — `POST /api/workflows/design` turns any stored
  process Analysis into an importable n8n workflow (intake webhook →
  agentic pipeline → risk-gated Slack escalation or reply, hourly
  monitoring digest, AI-mapping sticky note). Deterministic generation;
  `GET /api/workflows/{id}/download` returns the JSON for n8n's
  *Import from File*. This is the bridge from Side A ("should we
  automate this?") to Side B ("what does the automation look like?").
- **Approval-queue SLA metrics** — `GET /api/analytics/approvals` reports
  pending-review aging in four buckets, oldest pending age, human
  turnaround (mean/median/max), and escalation rate.
- **Run performance metrics** — `GET /api/analytics/runs` reports latency
  p50/p95/max, mean and p95 cost per run, failure rate, containment rate
  (tickets resolved with no human touch), and human-touch rate.
- **CSV exports for BI refresh** — `GET /api/export/runs.csv`,
  `approvals.csv`, and `usage.csv` with column schemas pinned by tests, so
  Power BI scheduled refresh survives API evolution.
- **Batch ticket ingestion** — `POST /api/tickets/batch` processes up to
  100 tickets per request with per-item isolation (one bad ticket never
  blocks the rest) and an aggregated summary.
- **Dashboard** — governance KPI cards (containment, escalation rate, p95
  latency, oldest pending approval), a "Design n8n workflow" action on
  every ROI analysis, and links to the CSV feeds.

### Fixed
- The quality-eval harness no longer leaks its rebound storage singleton
  into the host process — running `eval_quality.main()` inside a test
  suite left every later test reading a split-brain store.
- Demo script now stores its analysis, so estimated monthly savings in
  the monitoring summary reflects real numbers instead of $0.

### Stats
- 46 tests (up from 28), all green; routing eval still 8/8 with 100%
  escalation recall.

## [1.0.0] — 2026-09-16

### Added
- Side A: process analyzer with automation scoring, ROI/cost engine
  (labor + token economics) emitting assumptions next to every figure.
- Side B: 6-agent support pipeline (intake → knowledge/RAG → decision →
  draft → quality → action) orchestrated with LangGraph.
- Deterministic governance gates: monetary limit, security actions,
  weak retrieval, low confidence — always human review, independent of
  model confidence; enforced in code and pinned by tests.
- Human-in-the-loop approval queue (queue → decide → execute/reject)
  with append-only audit log.
- Integrations: CRM, email, billing, Slack adapters with an outbox.
- RAG over `knowledge_base/`: heading-aware chunking, TF-IDF cosine
  retrieval, live-embeddings path.
- Mock/live LLM gateway — the whole platform runs offline in mock mode;
  `AIOPS_MODE=live` + `OPENAI_API_KEY` swaps every call to a real model.
- FastAPI service + operator dashboard, n8n ticket-intake bridge,
  Docker Compose (API + n8n), quality eval harness with CI gate,
  docs (architecture, governance, operations, BI integration).
