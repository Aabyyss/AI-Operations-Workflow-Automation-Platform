# Architecture

## Component map

```text
                     ┌─────────────────────────────┐
                     │  dashboard/index.html       │  operator UI
                     └──────────────┬──────────────┘
                                    │ fetch()
                     ┌──────────────▼──────────────┐
                     │  FastAPI (backend/main.py)  │  /api/analyze, /api/tickets,
                     └──────┬───────────────┬──────┘  /api/reviews, /api/analytics
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
— `store.py` is the only thing that changes.

**LangGraph as visualization, not gospel.** `pipeline.py` is the behavioral
source of truth; `graph.py` expresses the same routing as an inspectable
graph (`intake → knowledge → decision → auto|human`). One source of truth
avoids drift between "what the code does" and "what the diagram says".

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
```

## Extension points

| To add...                  | Touch...                              |
|----------------------------|---------------------------------------|
| A new business system      | `integrations/actions.py` + outbox    |
| Real vector DB             | `rag.py` (`Retriever` only)           |
| Postgres                   | `store.py` (interface is 5 methods)   |
| New knowledge              | drop a `.md` in `knowledge_base/` + `POST /api/knowledge/reload` |
| A new risk rule            | `pipeline.run_decision`               |
| A new agent                | a `run_*` function + a graph node     |
