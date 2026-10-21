# Testing Guide

What the suite pins, how to run it, and how to extend it without
weakening it. Current state: **354 tests across 44 files**, green in CI on
Python 3.11 / 3.12 / 3.13, plus an eval-quality gate. Everything runs
offline — no test touches the network.

---

## 1. Running it

```bash
python -m pytest                          # the whole suite
python -m pytest tests/test_roles.py -q   # one file
python -m scripts.eval_quality            # governance eval: 8/8 routing accuracy, 100% escalation recall
python -m scripts.demo                    # end-to-end walkthrough (also smoke-tests the pipeline offline)
```

The eval harness is a **regression gate, not a unit test**: it scores
routing accuracy and escalation recall over a labeled ticket set and
fails the `eval-quality` CI job if either drops. A prompt or heuristic
change that "fixes" one ticket while breaking escalation recall does not
ship.

## 2. What the suite actually pins

| Layer | Files | The failure it's designed to catch |
|---|---|---|
| Governance / routing | `test_pipeline.py`, `test_eval.py` | money ≥ limit, 2FA changes, weak retrieval or low confidence no longer escalating |
| API contracts | `test_api.py`, `test_health.py`, `test_knowledge.py`, `test_roles.py`, `test_ab.py`, `test_quality_gate.py` | endpoint shape drift, wrong status codes, role gates silently removed |
| Schema-pinned exports | `test_exports.py` | a CSV column change silently breaking a scheduled Power BI refresh |
| Analytics math | `test_run_metrics.py`, `test_approval_sla.py`, `test_budget.py`, `test_drift.py`, `test_ab.py`, `test_quality_gate.py` | percentile / projection / swing regressions |
| Security | `test_security.py`, `test_roles.py`, `test_webhook.py`, `test_ratelimit.py` | auth bypass, signature or replay-window regressions, role scoping errors |
| Storage & ops | `test_backup.py`, `test_retention.py`, `test_ready.py`, `test_health.py` | prune destroying unparseable records, backup/restore breakage, component-report drift |
| Shareable artifact safety | `test_one_pager.py` | a document that leaves the building and is never re-derived: the CLI and the endpoint must produce byte-identical HTML (one renderer, no drift), a hostile vendor string must be escaped rather than becoming markup, and the honesty sections — sample floor, "where we lose", quote-required plans never rendering as `$0.00` — must be present |
| Cost model honesty | `test_cost_compare.py` | a comparison that silently wins: per-outcome/seat/flat arithmetic, the crossover rate, a quote-only plan priced at zero, and the honesty rules (sample floor, mock-mode caveat, every preset cited, losing plans named). `plans_beaten` and `plans_that_beat_us` are asserted as inverses so neither can quietly count the same outcome |
| Storage backends | `test_pgstore.py` | SQL shape, insertion order, id-keying, transactional `replace_all`, and pgvector-unavailable degradation — all pinned with offline cursor/connection stubs, so the Postgres path is tested without a database. Also pins that `AIOPS_STORAGE=json` still builds the JSON store, and that a misconfigured Postgres setup fails with an actionable message instead of a stack trace |
| Delivery & loop | `test_outbound.py`, `test_feedback.py`, `test_replay.py`, `test_stream.py`, `test_metrics_endpoint.py`, `test_reqlog.py`, `test_model_routing.py` | double-send, dead-letter mishandling, replay disposition diffs missing |
| Decision receipts | `test_ed25519.py`, `test_receipts.py`, `test_receipt_endpoints.py` | a signature scheme is only worth what its primitives are, so Ed25519 is checked against the RFC 8032 vectors byte-for-byte, then the attacks: flipped message, flipped signature, wrong key, the malleable `S + L` variant, small-order keys, truncation. Receipts are tested by tampering — risk score, disposition, a gate threshold, an internally consistent forgery signed with another key — plus chain deletion/reordering, a re-signed broken link, `require_root`, a signing failure landing on the run, and the CLI run as a subprocess against a downloaded file (exit 1 on tamper, exit 2 on bad input) |
| Shadow mode | `test_shadow.py` | a "safe" scoring path that quietly queues a refund, sends an email, creates a review or writes a run record would look safe while moving the numbers it exists to measure. So absence is asserted first — runs, reviews, tickets, outbox and receipts all empty after scoring — then the would-have arithmetic, the triggers, the empty-window wording, and that `AIOPS_SHADOW` leaves the run ledger untouched |
| Artifact provenance | `test_one_pager_artifact.py`, `test_migrate.py` | the committed one-pager verifies against its sidecar and re-renders byte-for-byte; a hand-edited figure fails the digest, a renderer change fails the re-render, an unknown manifest version is refused, and the CLI exits 1 on drift. The migration's `--embeddings` step refuses without a model and refuses an empty corpus, because an empty vector table reads as "no policy matched" |
| Retrieval backends | `test_vectors.py`, `test_rag.py` | cosine in memory and pgvector's `<=>` agree in meaning (similarity, not distance), the SQL is asserted with a stubbed cursor (extension, unique key, DELETE-then-upsert, ORDER BY, literal encoding), and every degradation path is pinned: no key → TF-IDF, failed index build → TF-IDF, provider error mid-search → TF-IDF, `>=0` still answers tickets |
| Dashboard wiring | `test_dashboard_wiring.py` | what the page talks to, not how it looks: runs fed from runs (not tickets), feedback and replay keyed on the run id, the loaded cost shown wherever the verdict used it, receipts reachable from the row, the disposition vocabulary defined once, and the inline script parsed by `node --check` (skipped when node is absent) |
| Dashboard design system | `test_dashboard_theme.py` | the compiler this single-file dashboard never had. A broken `var()` renders as transparent black in one theme only, for the users in that theme, and nothing else catches it. So: both palettes define identical token names (the token added to one palette and forgotten in the other is invisible to whoever added it), no hex/`rgb()`/`hsl()` survives outside them, text and status tints clear computed WCAG contrast on every surface they sit on, the theme is resolved before the first paint, every visible field has a bound label, focus and reduced-motion are respected, and the scale ladders have no gaps. Writing it immediately found `color:#fff` on the primary button — 2.7:1 on the dark theme's accent |

## 3. Fixtures (`tests/conftest.py`)

- **`isolated_store`** — points `Storage` at a fresh temp directory per
  test. No test ever reads or writes a real `data/`.
- **`isolated_knowledge`** — **copies** the real `knowledge_base/*.md`
  corpus into a temp directory so knowledge-management tests can create,
  overwrite and delete documents freely. The repo corpus stays pristine;
  a regression that once polluted the real corpus during a test run is
  exactly what this fixture exists to prevent.

Knowledge endpoints must call `retriever.reload()` after every mutation —
tests assert the *retrieval index* reflects the change, not just the file.

## 4. Conventions

1. **Test the governance behavior, not the implementation.** The
   canonical assertion is "reject this review → no customer email is
   ever sent", not "the function returned False".
2. **Offline always.** Live mode is faked at the gateway; no sleeps, no
   external services, no randomness without a seed.
3. **Schema-pin anything a consumer depends on** — CSV columns, JSON
   response shapes, the metrics exposition. If a dashboard or BI job
   reads it, a test fails before it drifts.
4. **New endpoint → four tests minimum:** happy path, auth/role gate,
   malformed input, and the empty-store case (fresh install must 200,
   not 500).
5. **Honest metrics are testable.** A/B below cohort size must report
   `sample_sufficient: false`; health checks must degrade, not 500.

## 5. CI (`.github/workflows/ci.yml`)

| Job | What it does |
|---|---|
| `test` (3.11 / 3.12 / 3.13) | full pytest suite per version, `fail-fast: false` so one version failing doesn't cancel the others; then the one-pager drift check, an API boot smoke test (ticket → receipt → offline verify), and a backup round trip (snapshot → `--verify` → restore) |
| `eval-quality` | `python -m scripts.eval_quality` — routing accuracy + escalation recall as a release gate |
| `visual` | `python -m scripts.visual_check --allow-cross-platform` — design tokens, headings and card inventory against `docs/screenshots/`; the capture and the JSON verdict come back as artifacts |

### 5.1 What cannot run in the pytest job

Three suites need a browser, a server or both. They are marked and skipped
with a reason, never silently passed:

| Suite | Needs | Run it with |
|---|---|---|
| `tests/test_receipt_viewer.py` browser cases | Chrome via `playwright-core` | `npm install --no-save playwright-core`, then `pytest tests/test_receipt_viewer.py` |
| `tests/test_visual_check.py` capture path | Chrome + a free port (the checker starts its own server) | `python -m scripts.visual_check` |
| the visual baseline itself | the platform that recorded `layout-baseline.json` | re-record with `--update` on that platform |

A skip is honest here because the *comparator* is still tested without a
browser; what is skipped is the capture, and the visual job exercises that on
Linux every push.

Concurrency cancellation is on: pushing again cancels the superseded
run. The badge in the README is the suite, not a subset.

## 6. When you add a feature

The [definition of done](project-plan.md#6-definition-of-done-per-feature)
requires tests that cover the **governance behavior** of the change —
if the feature can misroute a ticket or leak data, that test comes
first. If you find yourself weakening an assertion to make a change
pass, stop: the assertion is documenting a real consumer.
