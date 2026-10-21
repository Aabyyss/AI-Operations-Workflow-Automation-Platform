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

## D16 · Theme the dashboard through tokens, and test the theme like code

- **Context:** the dashboard is one self-contained HTML file — no build step, no CDN, no framework, which is what lets it run on a machine with nothing but Python and a browser. The cost is that nothing checks it: there is no compiler, no linter and no type system between an edit and a broken palette. The first light theme proved it, shipping as the dark palette inverted and reached through a media query that only fired when the theme attribute was *absent* — so `auto` on a light OS silently got the dark-designed page, and `#fff` sat on the light-blue accent at 2.7:1.
- **Decision:** all visual values become CSS custom properties in two layers — theme-independent scales (space, radius, type, motion, layout) and a semantic colour vocabulary defined once per theme. **No colour literal may exist outside the two palette blocks**, both palettes must define exactly the same token names, and the active preference is *resolved to a concrete theme* and written to `data-theme` in `<head>`, so the stylesheet never guesses and there is one code path per theme. The rules are enforced by `tests/test_dashboard_theme.py`, including WCAG contrast computed rather than eyeballed.
- **Consequence:** a second theme is a palette swap instead of a second stylesheet, and bright mode is designed as daylight (elevation instead of lines) rather than as an inversion. Cost: contributors must add a token to both palettes before using it, and the test is the arbiter — it rejected three of its own first-draft assertions during this work, and caught `color:#fff` on the primary button before a human did.

## D17 · Sign the decision, not the conversation — and make it verifiable offline — v1.6

- **Context:** the audit log answers "what happened" for an operator who already trusts this deployment. A customer, auditor or vendor evaluator does not. Asking them to call an API and trust the response is not evidence; emailing them a PDF is not verifiable. And a receipt carrying the customer's message and address is a liability with a timestamp on it, because it leaves the building.
- **Decision:** every pipeline decision produces an Ed25519-signed receipt (`backend/receipts.py`) covering the disposition, the risk score and its reason, **the gate thresholds that were in force**, the proposed actions, the quality verdict, the retrieved document ids and the response digest — never the response text, and with addresses redacted from action ids before signing. Receipts chain by digest, so a deletion or reorder is visible to whoever holds a later one. `ed25519.py` is RFC 8032 in pure Python with `S < L` and on-curve checks, checked against the RFC's vectors, because the verification tool must run on a machine that has nothing but Python. `scripts/verify_receipt.py` verifies a file with no server, no database and no shared secret.
- **Consequence:** the strongest claim the platform can make — "this is what the system decided, and here is why" — becomes something a third party can check years later without trusting us. Costs: signing adds a small failure mode (recorded on the run as `receipt_error`, never swallowed), key rotation is not yet supported without invalidating old receipts against the current published key, and the receipt is only as meaningful as the gates it records.

## D18 · Shadow mode executes nothing, and says so in its own disposition — v1.6

- **Context:** "what would it have escalated?" is the question that decides whether an agent gets real work. Answering it with a reimplementation, or with a mode that quietly queues reviews, would either drift from the real pipeline or move the very metrics it exists to measure.
- **Decision:** `AIOPS_SHADOW=1` routes `/api/tickets` through the same `run_intake`, `run_knowledge` and `run_decision` functions and stops there: no draft, no email, no refund, no review, and no run record. Shadow records go to their own collection with their own disposition (`shadow_scored`), so containment, escalation and failure rates cannot move. The report names the would-have escalations with their triggers, and the empty state says "no tickets scored yet" rather than 0%.
- **Consequence:** an operator can leave it on during a real evaluation with no blast radius, and the number they read is produced by the code they will later trust with the actions. Cost: intake and retrieval cost is still incurred (it has to be, to score), and a shadow decision is not a receipt — nothing was executed to attest to.

## D19 · Bill handoff labour on both sides, or on neither — v1.6

- **Context:** the cost comparison charged the pipeline for the humans it escalates to, and left every per-outcome vendor's *unresolved* tickets free. Those tickets land on the same agents, so at equal inputs the model flattered whoever billed last — the opposite of its purpose.
- **Decision:** `include_human_review_cost` prices both queues at one rate: our measured escalations and the vendor's unbilled tickets. Each plan carries its bill *and* a fully loaded figure; verdicts, the cheapest plan and the win/loss counts all use the loaded number so nothing compares a loaded cost against a bare one. Because a plan's cost is linear in the share it resolves, the crossover's slope depends on whether a human touch costs more than the per-outcome price — when it does, resolving more *saves* money, the crossover is a floor, and the one-pager says "fewer than".
- **Consequence:** a cheap meter can no longer win by hiding its queue, and the crossover note can be read in the direction it actually points. Cost: with labour included, the comparison is sensitive to the buyer's assumed human cost per touch — which is why it is an explicit input, billed identically on both sides, and stated in the assumptions.

## D20 · TF-IDF is the floor, not the fallback story — v1.6

- **Context:** live mode rebuilt embeddings in memory on every boot, and the Postgres backend shipped a pgvector schema nothing read. A deployment could therefore believe it was on semantic retrieval while serving keyword search, and an embeddings outage could take ticket handling down entirely.
- **Decision:** `backend/vectors.py` puts `replace(chunks, vectors)` and `search(vector, k)` behind one interface with two implementations — in-process cosine, or pgvector's `<=>` (converted back to a similarity so one threshold means the same thing) — and `rag.py` always fits TF-IDF first, treating vectors as an upgrade it will fall back from on any error. `/ready` reports which backend is actually serving. `--embeddings` on the migration script fills the table once, and refuses loudly when there is no model rather than leaving an empty index that reads as "no policy matched".
- **Consequence:** zero-config stays zero-config, a provider outage degrades quality instead of availability, and "we are on semantic search" is a claim the readiness probe can substantiate. Cost: two retrieval paths must keep returning the same shape, and pgvector needs the extension, the driver and a DSN before it can be used at all.

## D21 · Evidence outlives the deployment that made it

- **Context:** two features that keep the past trustworthy were quietly
  unguarded. Retention pruned every collection it was told about, and a signed
  receipt is not an operational record — someone else holds a copy, plus its
  chain link, so pruning this deployment's copy breaks a verification the
  auditor performs on their own machine. Meanwhile backups ran on a schedule
  and nothing had ever read one back: a truncated archive, or a snapshot
  holding the signing seed without the published history, looked exactly like a
  good backup until the day it was needed.
- **Decision:** `receipts` stays out of `COLLECTION_LIMITS`, and a test prunes
  every covered collection to zero to prove it. Snapshots recurse (the old
  top-level walk silently omitted anything in a subdirectory) and `--verify`
  reopens one to count collections, refuse unparseable JSON, and refuse a
  snapshot holding only one half of the seed/published-history pair — restoring
  half leaves a deployment that will not sign and cannot say why. `--restore-to`
  extracts only into an empty directory, resolves every member under it, and
  leaves the final move into `data/` to a human.
- **Consequence:** "we have backups" becomes a claim CI checks on every push,
  and evidence cannot be lost to a retention run. Cost: snapshots carry a
  secret (the signing seed) and need the care of `data/` itself, and restore is
  deliberately two steps rather than one command that can overwrite live data.

## D22 · A capability with no clock is a capability nobody uses

- **Context:** key rotation shipped as an operation with no schedule and no
  signal. Everything about it was designed so rotation is safe — old receipts
  verify against the published history and report themselves as retired — and
  that safety is exactly why forgetting it is easy: nothing breaks, so nothing
  reminds you.
- **Decision:** the active key's age is reported where an operator already
  looks (`/api/health` → `checks.receipts`), the report turns `degraded` past
  `AIOPS_RECEIPT_KEY_MAX_AGE_DAYS` (365 by default, `0` disables), and
  `scripts/rotate_receipt_key --check` gives cron the same answer as exit codes:
  `0` inside the limit, `1` overdue, `2` cannot tell. Age is read from the
  published history, which never writes a file — asking for health must not
  create a key. A key supplied through `AIOPS_RECEIPT_KEY` is reported as
  `managed_by: environment` and is never called overdue: it cannot be dated
  here, and rotating it is not this deployment's decision.
- **Consequence:** the policy ("rotate yearly") is now something the system can
  observe and a monitor can page on, and "cannot tell" is a distinct answer
  rather than a silent pass. Cost: one more threshold to set, and the honest
  admission that an env-managed key's age is invisible to this process.
