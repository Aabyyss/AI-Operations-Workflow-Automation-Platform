# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning follows [SemVer](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [v1.2.0] — 2026-09-22 — production hardening

Everything in this release is opt-in: the platform still runs offline in
mock mode with zero configuration. Each feature exists because a real
deployment would be asked for it before go-live.

### Added
- **Optional API-key auth** (`AIOPS_API_KEY`): constant-time-key
  comparison, dashboard header passthrough, 401 with
  `WWW-Authenticate: Bearer` when enabled.
- **Signed n8n intake** (`POST /api/tickets/signed`): HMAC-SHA256
  signature + timestamp replay window (`AIOPS_WEBHOOK_SECRET`,
  `AIOPS_WEBHOOK_MAX_SKEW`), constant-time verification, mirror of the
  plain intake contract.
- **Opt-in rate limiting** (`AIOPS_RATE_LIMIT`, `AIOPS_RATE_WINDOW`):
  fixed-window per-IP limiter, 429 with `Retry-After`, `X-RateLimit-*`
  headers, exempt `/health`.
- **Structured request logging** (`AIOPS_REQUEST_LOG_FILE`): one JSON
  line per request with request-ID propagation, latency, status, and
  auth/rate-limit rejections included.
- **`/ready` readiness probe**: checks the RAG corpus is loaded and the
  JSON store is writable — `/health` (liveness) now has a real partner.
- **Backup tooling** (`scripts/backup.py`): timestamped tar.gz snapshots
  of the store, retention pruning, `--list`, `--retention`,
  `AIOPS_BACKUP_DIR`.

### Changed
- CI hardening: `actions/checkout@v5` / `setup-python@v6` (clears the
  Node 20 deprecation warnings), `fail-fast: false` so one Python
  version failing no longer cancels the matrix, concurrency cancellation
  for superseded runs.
- Docker image ships the backup script; compose wires the security and
  logging settings through and adds `restart: unless-stopped` plus a
  `start_period` on the healthcheck.
- `.env.example` documents every new variable, grouped by concern.

### Fixed
- None in this release; v1.1.0 shipped the latency-precision fix.

## [v1.1.0] — 2026-09-22 — closing the operations loop

- Workflow designer: Analysis → importable n8n JSON
  (`POST /api/workflows/design`).
- Approval-queue SLA metrics: aging buckets, human turnaround,
  escalation rate (`/api/analytics/approvals`).
- Run performance analytics: latency p50/p95/max, cost per run,
  failure / containment / human-touch rates (`/api/analytics/runs`).
- CSV export feeds for Power BI with schema pinned by tests
  (`/api/export/{runs,approvals,usage}.csv`).
- Batch ticket ingestion (`POST /api/tickets/batch`, ≤ 100 items).
- Dashboard: containment/p95 KPIs, workflow designer button, BI feed links.
- Latency measured with `perf_counter` at sub-millisecond precision
  (mock-mode runs no longer collapse to 0 ms on fast machines).

## [v1.0.0] — 2026-09-21 — initial platform

- Typed contracts, config, JSON store, mock/live LLM gateway, ROI engine.
- RAG layer: markdown ingestion, heading-aware chunking, TF-IDF cosine
  retrieval, live-embeddings path.
- Agentic support pipeline: 6 agents, deterministic risk gates,
  LangGraph graph, CRM/email/billing/Slack adapters, audit log.
- Operator dashboard, end-to-end demo, Docker + n8n compose, CI.

[v1.2.0]: https://github.com/Aabyyss/AI-Operations-Workflow-Automation-Platform/compare/v1.1.0...v1.2.0
[v1.1.0]: https://github.com/Aabyyss/AI-Operations-Workflow-Automation-Platform/compare/v1.0.0...v1.1.0
[v1.0.0]: https://github.com/Aabyyss/AI-Operations-Workflow-Automation-Platform/releases/tag/v1.0.0
