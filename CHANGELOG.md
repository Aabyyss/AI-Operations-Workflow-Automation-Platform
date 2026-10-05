# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning follows [SemVer](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [v1.4.0] — 2026-10-06 — reliability, knowledge ops, honest metrics

This release answers the questions an operator or buyer asks next:
"which part is broken right now?", "who is allowed to do what?",
"can we fix a stale policy without a redeploy?", and — the uncomfortable
one — "is the AI actually faster, and is the risk gate actually right?"
As always: everything ships off-by-default-safe, the demo still runs with
zero configuration, and the metrics refuse to lie about sample size.

### Added
- **Component health report**: `GET /api/health` reports each moving part
  with a machine-readable status — storage writability with per-collection
  record counts, knowledge corpus chunks, approval-queue backlog (≥ 100
  pending → degraded), outbox dead letters, outbound delivery config, LLM
  gateway mode/tiers, and budget posture. Checks degrade loudly instead of
  erroring; the dashboard renders the strip live.
- **Knowledge management API**: list/read/create/delete policy documents
  over `GET/POST /api/knowledge` and `GET/DELETE /api/knowledge/{doc_id}`.
  Slugs are traversal-proof — path-like names are rejected outright, not
  silently rewritten — replacing a document requires an explicit
  `overwrite` flag (409 otherwise), and the retriever reloads before every
  response, so the next ticket retrieves from the new corpus. Dashboard
  knowledge manager included.
- **Role-scoped API keys**: `AIOPS_APPROVER_KEY` (read + review decisions)
  and `AIOPS_ADMIN_KEY` (prune) below the master key, with constant-time
  role resolution and route-level scoping — `decide_review` requires the
  approver role, `prune` requires admin. Auth disabled ⇒ everything open,
  local dev unchanged.
- **A/B cycle-time analytics**: tickets/runs accept `cycle_seconds`
  (ai_assisted cohort); `POST /api/analytics/ab/records` stamps the manual
  cohort into a new `cycle_times` collection. `GET /api/analytics/ab`
  publishes median/p90/mean — and withholds any "% faster" figure below
  five samples per cohort, saying so instead.
- **Quality-gate metrics**: `GET /api/analytics/quality` turns the review
  ledger into gate precision (rejected / decided escalations) and a recall
  proxy from feedback (thumbs-down auto-resolutions / auto-resolutions).
  Measured from outcomes only — no re-scoring, works offline.
- **Desktop launcher + shortcut (Windows)**: `scripts/launch_aiops.cmd`
  serves on :8200 with a single-instance guard, `/health` wait and
  `data\launcher.log`; `scripts/create_desktop_shortcut.ps1` installs an
  idempotent "AI Ops Platform" Desktop shortcut.
- **Docs**: `docs/DATA_MODEL.md` (every collection's writer, shape and
  retention cap), `docs/DECISIONS.md` (decision log), `docs/TESTING.md`
  (testing guide).

### Fixed
- Dashboard: `designWorkflow` was missing its closing brace — shipped in
  v1.3.0 as a script parse error that left **every** panel stuck on
  "Loading…". All panels render again.

### Changed
- Test suite 116 → 149 (33 new across health, knowledge, roles, A/B and
  gate-quality tests; `cycle_times` is the 11th store collection).
- `.env.example` documents the role keys; OPERATIONS, ARCHITECTURE and
  project-plan refreshed for v1.4.0.

## [v1.3.0] — 2026-10-01 — the learning loop

The platform no longer just runs and reports — it can be *questioned*.
Every automated answer can collect human feedback, be replayed against
the current model/policy/thresholds, and be watched for behavioral
drift. Spend gets a budget with projection alerts; queued actions get
signed outbound delivery with a retry budget; data gets a retention
policy. As always: every feature is off-by-default-safe, and the demo
still runs with zero configuration.

### Added
- **Feedback capture**: `POST /api/runs/{id}/feedback` (thumbs + optional
  correction), audit-logged; satisfaction ratio, correction count and
  coverage joined into `/api/analytics/runs`.
- **Run replay**: `POST /api/runs/{id}/replay` re-executes the original
  ticket and diffs disposition and response — proof, not hope, that a
  prompt or policy change preserved behavior.
- **SSE run stream**: `GET /api/runs/stream` tails new runs (backfills
  recent history on connect), hand-rolled on `StreamingResponse` — zero
  new dependencies.
- **Prometheus `/metrics`**: dependency-free text exposition of
  throughput by disposition, latency quantiles, cumulative cost, queue
  depth, feedback counts, corpus size.
- **Outbound webhooks**: `AIOPS_OUTBOX_URL` turns the outbox into signed
  HTTP deliveries (same HMAC scheme as intake), with per-attempt ledger,
  exponential-retry accounting, dead-letter state and operator reset
  (`POST /api/outbox/{id}/retry`). Delivered records are never re-sent.
- **Model routing**: `AIOPS_LIGHT_MODEL` for classification-shaped work
  (intake, quality gates), `AIOPS_HEAVY_MODEL` for customer-facing
  drafts; inert until set, visible per-call in usage records.
- **Budget guardrails**: `AIOPS_MONTHLY_BUDGET_USD` +
  `GET /api/analytics/budget` — spend, linear month projection, alert at
  100% spend or 120% projection.
- **Drift canary**: `GET /api/analytics/drift` compares the last N runs
  to the N before them on containment, escalation and failure; ±10pp
  swings raise the alarm.
- **Retention**: `POST /api/admin/prune` (dry-run by default) applies
  per-collection age/size caps; unparseable timestamps are kept, never
  silently destroyed; executed prunes are audit-logged.
- **Dashboard**: satisfaction KPI, budget meter, drift-canary panel,
  per-run 👍/👎/Replay actions, and a light/dark/auto theme toggle.

### Changed
- Demo script gained a learning-loop section; OPERATIONS/BI_INTEGRATION
  document every new endpoint; project plan records milestones 18–24.
- `.env.example` documents the routing, budget, outbox and retention
  settings.

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

[v1.4.0]: https://github.com/Aabyyss/AI-Operations-Workflow-Automation-Platform/compare/v1.3.0...v1.4.0
[v1.2.0]: https://github.com/Aabyyss/AI-Operations-Workflow-Automation-Platform/compare/v1.1.0...v1.2.0
[v1.1.0]: https://github.com/Aabyyss/AI-Operations-Workflow-Automation-Platform/compare/v1.0.0...v1.1.0
[v1.0.0]: https://github.com/Aabyyss/AI-Operations-Workflow-Automation-Platform/releases/tag/v1.0.0
