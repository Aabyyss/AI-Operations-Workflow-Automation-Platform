# Architecture

## Component map

```text
                     ┌─────────────────────────────┐
                     │  dashboard/index.html       │  operator UI — health strip, quality +
                     └──────────────┬──────────────┘  A/B panels, knowledge manager, theme
                                    │ fetch() (+ X-API-Key when auth on)
                     ┌──────────────▼──────────────┐
                     │  FastAPI (backend/main.py)  │  /api/analyze, /api/tickets,
                     │  + security.py gate         │  /api/reviews, /api/knowledge,
                     └──────┬───────────────┬──────┘  /api/analytics, /api/health
                            │               │
          Side A            │               │            Side B
   ┌────────────────────────▼──┐   ┌────────▼─────────────────────┐
   │ analyzer.py               │   │ pipeline.py (6 agents)       │
   │   └ roi.py (cost engine)  │   │   intake → knowledge →       │
   │   scoring + assumptions   │   │   decision → draft →         │
   └───────────────────────────┘   │   quality → actions /        │
                                   │   escalation                 │
                                   └──────┬───────────┬───────────┘
                                          │           │
                              ┌───────────▼──┐   ┌────▼─────────────────┐
                              │ rag.py       │   │ integrations/        │
                              │ TF-IDF/live  │   │ crm · email ·        │
                              │ embeddings   │   │ billing · slack      │
                              └───────┬──────┘   └────┬─────────────────┘
                                      │               │
                              knowledge_base/    outbox + audit log
```

### Reliability & measurement layer (v1.4)

```text
  security.py       optional API-key gate + role scoping (operator/approver/admin)
  health.py         component report: storage, corpus, queue, outbox, gateway, budget
  knowledge.py      corpus CRUD: traversal-proof slugs, overwrite flag, reload-on-mutation
  ab_testing.py     cycle-time cohorts (ai_assisted vs manual), honest-sample gating
  quality_gate.py   gate precision + recall proxy from review/feedback outcomes
```

## Design decisions

**Typed contracts everywhere.** Every agent hand-off is a Pydantic model
(`backend/models.py`). The API, the pipeline, and the dashboard all speak the
same shapes — that is what makes the system auditable and swappable.

**Mock/live duality.** `llm.py` exposes one interface; `MockLLM` implements
deterministic heuristics, `OpenAILLM` calls any OpenAI-compatible endpoint.
Agents cannot tell the difference. Result: the entire demo runs offline,
tests are hermetic, and "go live" is an env-var change, not a refactor.

**Governance is deterministic.** Risk scoring (`run_decision`) is rule-based
on purpose: money thresholds, security-controlled actions, retrieval
confidence. A model's self-reported confidence is an *input* to risk, never
the decision-maker. This is the enterprise pattern regulators expect.

**RAG without heavyweight deps.** `rag.py` implements real TF-IDF + cosine
retrieval (chunked on `##` headings) so scoring is honest math, with a
drop-in live-embeddings path. Swapping to pgvector later touches one file.

**Storage behind one class.** JSON files today, SQLAlchemy/Postgres tomorrow
— `store.py` is the only thing that changes. Eleven collections today;
[counts, writers and retention caps](DATA_MODEL.md) live in one table.

**Security is layered, opt-in, and asymmetric.** The middleware gate
(`security.install_auth`) authenticates any configured key; route
dependencies then narrow scope — `require_approver` on review decisions,
`require_admin` on prune/reload. With auth disabled the dependencies pass
through as "open", so local dev needs zero config.

**Measurement is honest by default.** The A/B report refuses to publish a
"% faster" number under five samples per cohort; the quality gate reports a
*recall proxy* and says so in its own response; health checks degrade
instead of erroring. Numbers this system publishes must survive a skeptic.

**LangGraph as visualization, not gospel.** `pipeline.py` is the behavioral
source of truth; `graph.py` expresses the same routing as an inspectable
graph (`intake → knowledge → decision → auto|human`). One source of truth
avoids drift between "what the code does" and "what the diagram says".

The full context-and-consequence list for every one of these lives in the
[decision log](DECISIONS.md).

## Data flow for one ticket

```text
POST /api/tickets
  → IntakeAgent      classify (category/priority/intent/amount)   ~$0.0001
  → KnowledgeAgent   retrieve policy chunks (score ≥ 0.15)
  → DecisionAgent    risk = f(money, security, confidence, retrieval)
        risk ≤ 0.30 → Draft → Quality (grounded/tone/PII) → Actions
        risk > 0.30 → Draft → ReviewRequest → human queue (nothing sent)
  → Actions          outbox: refund / email / CRM + audit events
  → PipelineResult   persisted to runs/ + usage/ (monitoring feed)

Measurement surfaces that watch the same data (no second pipeline):
  GET /api/health              component report (storage → budget posture)
  GET /api/analytics/ab        cycle-time: ai_assisted runs vs manual records
  GET /api/analytics/quality   gate precision from reviews, recall proxy from feedback
```

## Extension points

| To add...                        | Touch...                                                        |
|----------------------------------|-----------------------------------------------------------------|
| A new business system            | `integrations/actions.py` + outbox                              |
| Real vector DB                   | `rag.py` (`Retriever` only)                                     |
| Postgres                         | `store.py` (interface is 5 methods)                             |
| New knowledge                    | drop a `.md` in `knowledge_base/` + `POST /api/knowledge/reload` — or create/update it over `POST /api/knowledge` |
| A new risk rule                  | `pipeline.run_decision`                                         |
| A new agent                      | a `run_*` function + a graph node                               |
| A new role                       | `security.py` — add a key in `config.py` + a `_scope_factory` dependency |
| A new health check               | `health.health_report()` — one dict with a `status` field        |
| A new measurement                | a `backend/*.py` calculator + one read-only route               |
