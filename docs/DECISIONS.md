# Decision Log

The architectural calls this project has made — the context that forced
them, the decision, and what each one costs. Newest last. Thresholds live
in [GOVERNANCE.md](GOVERNANCE.md); storage shapes in
[DATA_MODEL.md](DATA_MODEL.md); the module map in
[ARCHITECTURE.md](ARCHITECTURE.md).

---

## D1 · JSON files behind one `Storage` class — v1.0

- **Context:** the system must run with zero setup (demo, CI, grading) but must not paint itself into a persistence corner.
- **Decision:** plain JSON files under `data/`, every collection accessed through `backend/store.py`. No other module touches persistence directly.
- **Consequence:** zero-config startup and hermetic tests. Postgres later means implementing one small interface, not a migration project. Cost: no concurrent-writer story — acceptable for a single-instance deployment, and the reason `/ready` probes writability.

## D2 · Mock/live LLM duality, offline by default — v1.0

- **Context:** nobody should need an API key to evaluate, test, or demo the platform; going live must be a deployment choice, not a code fork.
- **Decision:** one gateway interface (`llm.py`); `MockLLM` implements deterministic heuristics, `OpenAILLM` calls any OpenAI-compatible endpoint. Agents cannot tell them apart. `AIOPS_MODE=live` is the only switch.
- **Consequence:** the demo, the tests, and CI all run offline at $0; live quality is gated by the same deterministic rules as mock. Cost: mock results are only as good as the heuristics — which is why the eval harness pins them.

## D3 · Governance is deterministic; model confidence is an input, never the decider — v1.0

- **Context:** a model's self-reported confidence is not accountable to an auditor; money thresholds and security rules are.
- **Decision:** risk scoring (`pipeline.run_decision`) is rule-based — monetary limits, security-controlled actions, retrieval weakness, blended confidence floors. Escalation to a human is a code path, not a suggestion.
- **Consequence:** the routing behavior is testable, regressions fail CI (`tests/test_pipeline.py`, `scripts/eval_quality.py`), and "why was this escalated" always has a readable answer in the run trace.

## D4 · TF-IDF RAG, no vector database — v1.0

- **Context:** grounded drafts need retrieval, but heavyweight vector dependencies contradict the zero-setup goal.
- **Decision:** `rag.py` implements real TF-IDF + cosine over heading-chunked markdown, with a drop-in live-embeddings path behind the same `Retriever` interface.
- **Consequence:** scoring is honest math offline and testable in CI; swapping in pgvector touches one file. Cost: lexical retrieval misses paraphrases — the weak-retrieval escalation rule is the safety net.

## D5 · `pipeline.py` is the behavioral source of truth; LangGraph is the visualization — v1.0

- **Context:** orchestrators drift from what the code actually does the moment both are editable.
- **Decision:** `graph.py` expresses the same routing as an inspectable graph, but `pipeline.py` decides behavior. One source of truth.
- **Consequence:** "what the diagram says" and "what the code does" cannot diverge silently; the graph is documentation you can render, not an extra runtime to keep in sync.

## D6 · Security is opt-in — v1.2

- **Context:** local dev, the demo and CI need zero configuration; shared deployments need auth, signed intake, and rate limits.
- **Decision:** every protection (`AIOPS_API_KEY`, HMAC webhook signing, per-IP rate limiting, request logs) is off by default and documented in `.env.example`. Health endpoints stay keyless so probes never need secrets.
- **Consequence:** nothing breaks out of the box; the same flags a production auditor asks about are the ones the config documents.

## D7 · External actions go through an outbox, never inline — v1.3

- **Context:** a refund that fails mid-HTTP-call must not vanish, and a replay must not double-send.
- **Decision:** pipeline actions are appended to a durable `outbox` collection; delivery is a separate signed-webhook worker with a per-attempt ledger, retry budget, dead-letter state and operator reset.
- **Consequence:** crashes lose nothing, deliveries are auditable, replays are safe. Cost: actions are eventually-consistent by design.

## D8 · Retention is dry-run-first, audit-logged, and keeps what it cannot parse — v1.3

- **Context:** a prune bug that silently destroys records is unrecoverable.
- **Decision:** `POST /api/admin/prune` defaults to dry-run; `?confirm=true` executes and writes an audit event; records with unparseable timestamps are kept, never destroyed by a parsing guess.
- **Consequence:** retention is reversible-by-verification (dry-run output is the plan), and every real prune is attributable.

## D9 · Component health over boolean liveness — v1.4

- **Context:** `/health` answers "is it up"; the 2am question is "which part is wrong". A single boolean hides the answer.
- **Decision:** `GET /api/health` reports each component with machine-readable status — storage (with per-collection record counts), knowledge chunks, approval-queue backlog (≥ 100 pending → degraded), outbox dead letters, outbound delivery config, LLM gateway mode/tiers, budget posture — plus an overall `ok/degraded/error`.
- **Consequence:** the dashboard health strip and any uptime monitor render it without string-parsing. Checks degrade loudly instead of erroring the endpoint: analytics failing never 500s a health report.

## D10 · The knowledge corpus is data, managed over the API — v1.4

- **Context:** retrieval quality is only as good as the markdown; "edit files on the server, then call reload" is not an operator workflow.
- **Decision:** `backend/knowledge.py` backs list/read/create/delete endpoints. Slugs are traversal-proof — names containing paths, `..` or leading dots are **rejected**, not silently rewritten — and updates require an explicit `overwrite` flag (409 otherwise). Every mutation reloads the retriever before responding.
- **Consequence:** an operator can fix a stale policy from the dashboard and see the new chunks counted immediately. Cost: writes bypass git review — for audited environments, PR-based corpus changes remain the recommended path.

## D11 · Role-scoped keys, deliberately asymmetric — v1.4

- **Context:** one master key meant whoever could POST a ticket could also approve refunds and prune data.
- **Decision:** the master key (`AIOPS_API_KEY`) is the **operator** role. `AIOPS_APPROVER_KEY` grants read + review decisions; `AIOPS_ADMIN_KEY` grants admin mutations (prune, knowledge-corpus writes) but **never** approval decisions. Scoping is constant-time and applied as route dependencies; with auth disabled everything stays open so local dev is unchanged.
- **Consequence:** an approver cannot mint approvals or rewrite the corpus the AI drafts from; an admin cannot approve refunds. Delegation no longer means full trust.

## D12 · Honest A/B: no percentages below five samples — v1.4

- **Context:** the ROI engine *estimates* savings from assumptions; the natural follow-up question is "is the AI actually faster?" — and a two-data-point "% faster" is marketing, not measurement.
- **Decision:** `backend/ab_testing.py` compares cycle-time cohorts (`ai_assisted` from runs with `cycle_seconds`, `manual` via `POST /api/analytics/ab/records`). Below `MIN_COHORT_SIZE = 5` per cohort, the report says `sample_sufficient: false` instead of publishing a percentage.
- **Consequence:** the dashboard shows distributions (median/p90/mean) and an honest "not enough samples" state. Cost: the metric is boring for the first few days of real use — that is the point.

## D13 · Measure the gate from outcomes, not by re-scoring — v1.4

- **Context:** "how often is the risk gate right?" could invite re-running models over historical data — expensive, model-dependent, and circular.
- **Decision:** `backend/quality_gate.py` derives precision from the review ledger (rejected / decided escalations) and a recall proxy from feedback (thumbs-down auto-resolutions / auto-resolutions). No model access, works offline.
- **Consequence:** the metric is free, auditable, and moves only when reality moves. Cost: the recall proxy is a proxy — it undercounts escalations nobody complained about, which the endpoint's `note` field says out loud.

## D14 · The desktop launcher owns port 8200 — v1.4

- **Context:** double-clicking a shortcut must never fight a manually started `uvicorn` on :8000, and must never start a second instance of itself.
- **Decision:** `scripts/launch_aiops.cmd` serves on **8200**, checks for an existing listener first (reopens the dashboard instead of spawning a second server), waits up to 20s for `/health`, and logs to `data\launcher.log`. The desktop shortcut is an idempotent WScript `.lnk` created by `scripts/create_desktop_shortcut.ps1`.
- **Consequence:** one-click start for non-terminal users, zero port collisions with dev servers. Cost: the dashboard URL differs from the manual one — the launcher prints and opens the right URL every time.

## D15 · Price the market from cited list prices, and publish the losses — v1.5

- **Context:** the platform's strongest commercial argument is that per-outcome pricing ($0.99 a resolution, ~$2.00 a conversation) is unpredictable at scale while per-decision cost is measured. Making that argument in prose is marketing; a competitor comparison that always concludes "we win" is a brochure with a function signature, and any buyer can smell it.
- **Decision:** `backend/cost_compare.py` models each competitor from its **published list price with the source attached** (`MARKET_PLANS`, snapshot-dated), lets a caller override any rate with a real quote, and reads our side from the run ledger rather than an estimate. The report computes each plan's crossover resolution rate, **names the plans that come out cheaper than us**, and carries the platform's existing honesty rules: below `MIN_DECISIONS = 20` the measured cost is labelled *indicative*, and in mock mode it says the token cost is synthetic. A quote-only vendor (Sierra, Decagon, Forethought) is returned as `quote_required` rather than priced at zero.
- **Consequence:** the comparison is falsifiable — a buyer can re-derive every line, override a rate, and see the case where the pipeline loses. Cost: the numbers move when a vendor changes its pricing, so the snapshot date and sources are part of the output, and someone has to keep them current.
