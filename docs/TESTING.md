# Testing Guide

What the suite pins, how to run it, and how to extend it without
weakening it. Current state: **227 tests across 35 files**, green in CI on
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
| `tests` (3.11 / 3.12 / 3.13) | full pytest suite per version, `fail-fast: false` so one version failing doesn't cancel the others |
| `eval-quality` | `python -m scripts.eval_quality` — routing accuracy + escalation recall as a release gate |

Concurrency cancellation is on: pushing again cancels the superseded
run. The badge in the README is the suite, not a subset.

## 6. When you add a feature

The [definition of done](project-plan.md#6-definition-of-done-per-feature)
requires tests that cover the **governance behavior** of the change —
if the feature can misroute a ticket or leak data, that test comes
first. If you find yourself weakening an assertion to make a change
pass, stop: the assertion is documenting a real consumer.
