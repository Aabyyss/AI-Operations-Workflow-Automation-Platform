# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning follows [SemVer](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Market cost comparison** — `POST /api/analytics/cost-comparison` models
  what the AI-support market's pricing costs at *your* volume against what
  this platform actually costs per decision, read from the run ledger rather
  than estimated. Presets carry their published list prices **and their
  sources** (Intercom Fin $0.99/outcome, Zendesk AI seat tiers + outcome,
  Agentforce ~$2.00/conversation, Lorikeet $2,100/mo flat) plus a quote-only
  enterprise preset that refuses to be priced from nothing; any rate can be
  overridden with a real quote. For each plan it computes the resolution rate
  above which the plan overtakes the pipeline — the number a buyer should
  argue about — and the report **names the plans that come out cheaper than
  us** instead of only reporting wins. Honesty rules are inherited from the
  rest of the platform: below 20 recorded decisions the measured cost is
  labelled *indicative*, and in mock mode the report says its token cost is
  synthetic rather than implying a real invoice was measured. Every result
  carries its assumptions and caveats, is audited (`cost_comparison_modelled`),
  and renders as a dashboard panel.
- **`GET /api/analytics/market-plans`** — the dated list-price snapshot
  (sources included) so a UI or a buyer can inspect and override it.
- **Buyer-facing one-pager** — `GET /api/analytics/cost-comparison/one-pager.html`
  and `python -m scripts.cost_onepager` render the same comparison as a
  self-contained, print-ready page (no CDN, no JS, no images — safe to email
  or Ctrl+P). It is *generated*, never written by hand, so the document cannot
  disagree with the API a prospect might call themselves; both paths go
  through one renderer. The page leads with the crossover rate, then publishes
  a **"where this comparison says we lose"** section, the measurement basis and
  sample floor, and the caveats that would otherwise flatter us. A dashboard
  link regenerates it from whatever inputs are on screen.

## [v1.5.0] — 2026-10-07 — storage choice, tighter scope, honest dashboard

Three themes: the storage layer stops being a constraint, the knowledge
corpus gets the same role discipline as the approval queue, and the
operator dashboard stops overstating (and understating) what it knows.
JSON remains the default — every change here is opt-in, and the full suite
still runs offline with zero API keys.

### Added
- **Postgres storage backend (opt-in)** — `AIOPS_STORAGE=postgres` swaps
  every collection onto one JSONB `documents` table (`seq`, `collection`,
  `payload`, `created_at`) behind the *identical* seven-method `Storage`
  interface, so ids, shapes and retention behavior are unchanged. Insertion
  order is preserved via a monotonic `seq`; `update` is a shallow JSONB merge
  (`payload || %s::jsonb`) matching `dict.update`; `replace_all` is
  DELETE + INSERT in one transaction. `ensure_schema(with_pgvector=True)`
  creates the `vector` extension and a 1536-dim `embeddings` table — and
  **degrades instead of failing** when pgvector is unavailable (rollback,
  schema still created). DSN and `psycopg` are read lazily, so an unconfigured
  install never imports the driver. 14 contract tests pin the SQL shape,
  ordering, id-keying, transactional replace and all `build_storage()`
  branches using offline cursor/connection stubs.
- **`scripts/migrate_to_postgres.py`** — copies every collection from the JSON
  store through the same interface; `--dsn`, `--replace`, `--pgvector`, and
  `--verify` (compares per-collection counts and exits 1 on mismatch, 2 when
  no DSN is supplied).
- **pgvector Compose profile** — `docker compose --profile postgres up` starts
  a `pgvector/pgvector:pg16` service with a healthcheck and wires
  `AIOPS_STORAGE`/`AIOPS_DATABASE_URL` into the API container.
- **Competitor analysis and launch collateral** — `docs/COMPETITIVE_ANALYSIS.md`
  compares this platform against the resolution agents (Intercom Fin, Zendesk
  AI, Ada, Forethought, Decagon, Sierra, Zowie, Lorikeet), the workflow
  automation set (n8n, Zapier, Make, UiPath, Workato) and the agent
  observability tools (LangSmith, Langfuse, Arize) — sourced pricing, an
  explicit "where we lose" table, and a prioritized improvement roadmap.
  `marketing/linkedin-post.md` and a recorded
  `marketing/demo-dashboard-walkthrough.webm` (with shot list and narration
  script) ship alongside it.

### Changed
- **Knowledge corpus mutations are admin-scoped** — `POST /api/knowledge` and
  `DELETE /api/knowledge/{doc_id}` now require the admin role (or the master
  key). Reads and `/api/knowledge/reload` stay open to any valid key. An
  approver can still decide a refund but can no longer rewrite the policy the
  refund is approved against.
- **Dashboard audit feed rebuilt** — long payloads no longer run off the card
  edge. Event lines wrap (`overflow-wrap:anywhere`), truncation appends a real
  ellipsis instead of slicing mid-character, and payloads render as
  key/value pairs (`ticket_id tkt_… · actions [...]`) with the full JSON in a
  tooltip. Events are tone-coded; the card measures clean at
  `scrollWidth == clientWidth`.

### Fixed
- **Cost-by-agent showed `$0` for every agent** — `money()` caps at two
  decimals, so sub-cent token costs collapsed to zero and read as "free".
  A precision-aware formatter now keeps cents for dollars and goes to five
  decimals below a cent (`$0.00078`), with the exact figure in a tooltip.
- **Cost table was structurally invalid** — it emitted a row with 2 cells under
  4 headers plus an empty `colspan=2` row; now aggregates calls/tokens/cost per
  agent over a correct `colspan=4`.
- `docs/project-plan.md` milestone 11 ("Postgres + pgvector") still read
  `⬜ next` after the work shipped; now points at milestone 31.

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
